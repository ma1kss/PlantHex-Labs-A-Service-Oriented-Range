from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import stat
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import APIError, BuildError, ImageNotFound, NotFound

from cyber_range_core import get_logger


log = get_logger(__name__)


# Labels every SOR-managed Docker object carries
# Portainer / `docker ps --filter label=sor.managed=true` filters by these.
LABEL_MANAGED      = "sor.managed"
LABEL_KIND         = "sor.kind"          # challenge-image | challenge-session | challenge-net
LABEL_CHALLENGE_ID = "sor.challenge_id"
LABEL_USER_ID      = "sor.player_id"
LABEL_USERNAME     = "sor.player_username"


# Dockerfile parsing — minimal, just FROM and EXPOSE.
_FROM_RE   = re.compile(r"^\s*FROM\s+([^\s]+)", re.IGNORECASE | re.MULTILINE)
_EXPOSE_RE = re.compile(r"^\s*EXPOSE\s+(.+)$",  re.IGNORECASE | re.MULTILINE)


# Sync helpers (called from inside asyncio.to_thread)

def _client() -> docker.DockerClient:
    return docker.from_env()


def _is_safe_path(base: str, target: str) -> bool:
    base = os.path.realpath(base)
    target = os.path.realpath(target)
    return os.path.commonpath([base]) == os.path.commonpath([base, target])


def _safe_extract_tar(tar_path: str, dest: str) -> None:
    with tarfile.open(tar_path, "r:*") as tar:
        for m in tar.getmembers():
            if m.isdev():
                raise ValueError("Archive contains device file (blocked).")
            out = os.path.join(dest, m.name)
            if not _is_safe_path(dest, out):
                raise ValueError("Archive path traversal detected (blocked).")
        tar.extractall(dest)


def _is_docker_save_tar(tar_path: str) -> bool:
    """`docker save` tars contain manifest.json / oci-layout — they are NOT
    build contexts, and uploading one is almost certainly a user mistake."""
    try:
        with tarfile.open(tar_path, "r:*") as tar:
            names = set(tar.getnames())
        return "manifest.json" in names or "oci-layout" in names or "index.json" in names
    except Exception:
        return False


def _find_dockerfile(ctx: str) -> str | None:
    """Find a Dockerfile in the build context — root first, then any depth."""
    root = os.path.join(ctx, "Dockerfile")
    if os.path.exists(root):
        return root
    for dp, _, fns in os.walk(ctx):
        if "Dockerfile" in fns:
            return os.path.join(dp, "Dockerfile")
    for dp, _, fns in os.walk(ctx):
        for fn in fns:
            if fn.lower().endswith(".dockerfile"):
                return os.path.join(dp, fn)
    return None


def _sanitize_ctx(ctx: str) -> None:
    """Strip symlinks and non-regular files; normalise perms so the daemon
    can read everything regardless of the uploader's umask."""
    for dp, dns, fns in os.walk(ctx):
        for d in list(dns):
            p = os.path.join(dp, d)
            try:
                if os.path.islink(p):
                    os.unlink(p)
                    dns.remove(d)
                    continue
                os.chmod(p, 0o755)
            except OSError:
                pass
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                if os.path.islink(p):
                    os.unlink(p)
                    continue
                st = os.lstat(p)
                if not stat.S_ISREG(st.st_mode):
                    os.unlink(p)
                    continue
                os.chmod(p, 0o644)
            except OSError:
                pass


def _read_dockerfile(path: str, max_bytes: int = 2_000_000) -> str:
    with open(path, "rb") as f:
        data = f.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Dockerfile too large.")
    return data.decode("utf-8", errors="replace")


def _parse_base_image(text: str) -> str:
    m = _FROM_RE.search(text)
    if not m:
        raise ValueError("No FROM instruction in Dockerfile.")
    return m.group(1).strip()


def _parse_exposed_ports(text: str) -> list[str]:
    ports: list[str] = []
    for m in _EXPOSE_RE.finditer(text):
        for p in m.group(1).split():
            p = p.strip()
            if not p:
                continue
            if "/" not in p:
                p = f"{p}/tcp"
            if p not in ports:
                ports.append(p)
    return ports


def _image_exposed(cli: docker.DockerClient, tag: str) -> list[str]:
    try:
        img = cli.images.get(tag)
        ep = (img.attrs.get("Config", {}).get("ExposedPorts", {}) or {}).keys()
        return list(ep)
    except Exception:
        return []


# Public async API

@dataclass
class BuiltImage:
    image_tag:       str
    dockerfile_hash: str
    base_image:      str
    exposed_ports:   list[str]
    skipped_build:   bool


async def docker_socket_reachable() -> bool:
    """Used by the readiness probe."""
    def check() -> bool:
        try:
            _client().ping()
            return True
        except Exception:
            return False
    return await asyncio.to_thread(check)


async def ensure_player_network(network: str, subnet: str = "") -> None:
    """Create the dedicated bridge network for player containers if missing.

    Player containers are intentionally NOT on `sor_net` — that would put
    untrusted code one DNS lookup away from Consul, RabbitMQ, IAM and Postgres.
    Inter-container traffic is disabled (one trainee's container cannot reach
    another's) and the subnet is fixed so the host egress guard can confine
    it (SEC-001/SEC-002).
    """
    def ensure() -> None:
        cli = _client()
        try:
            cli.networks.get(network)
        except NotFound:
            ipam = None
            if subnet:
                ipam = docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(subnet=subnet)])
            cli.networks.create(
                network,
                driver="bridge",
                ipam=ipam,
                options={"com.docker.network.bridge.enable_icc": "false"},
                labels={LABEL_MANAGED: "true", LABEL_KIND: "challenge-net"},
            )
    await asyncio.to_thread(ensure)


async def build_image_from_archive(
    archive_path: str,
    *,
    challenge_id:        str,
    allowed_base_images: list[str],
) -> BuiltImage:
    """Extract → validate → build. Idempotent: if an image with the resulting
    tag already exists, skip the build and reuse it."""
    def work() -> BuiltImage:
        tmp = Path(tempfile.mkdtemp(prefix="sor_build_"))
        try:
            ctx = tmp / "ctx"
            ctx.mkdir()

            if _is_docker_save_tar(archive_path):
                raise ValueError(
                    "Archive looks like 'docker save' output (manifest/index detected). "
                    "Upload a build-context tar (Dockerfile + project files).",
                )

            _safe_extract_tar(archive_path, str(ctx))
            _sanitize_ctx(str(ctx))

            df_path = _find_dockerfile(str(ctx))
            if not df_path:
                raise ValueError("No Dockerfile found in archive.")

            df_text = _read_dockerfile(df_path)
            base    = _parse_base_image(df_text)
            ports   = _parse_exposed_ports(df_text)

            if allowed_base_images and "*" not in allowed_base_images:
                if base not in allowed_base_images:
                    raise ValueError(
                        f"Base image '{base}' not allowed. "
                        f"Allowed: {', '.join(allowed_base_images)}",
                    )

            df_hash = hashlib.sha256(df_text.encode()).hexdigest()
            tag     = f"sor/chal/{challenge_id}:{df_hash[:12]}"

            cli = _client()

            # Skip-build dedup: same tag already exists → reuse.
            try:
                cli.images.get(tag)
                exposed = ports or _image_exposed(cli, tag)
                if not exposed:
                    raise ValueError("Image has no EXPOSE — add EXPOSE in Dockerfile.")
                return BuiltImage(tag, df_hash, base, exposed, skipped_build=True)
            except ImageNotFound:
                pass

            try:
                cli.images.build(
                    path=str(ctx),
                    tag=tag,
                    rm=True,
                    forcerm=True,
                    pull=False,
                    labels={
                        LABEL_MANAGED:      "true",
                        LABEL_KIND:         "challenge-image",
                        LABEL_CHALLENGE_ID: str(challenge_id),
                    },
                )
            except (APIError, BuildError) as exc:
                raise ValueError(f"Docker build failed: {exc}")

            if not ports:
                ports = _image_exposed(cli, tag)
            if not ports:
                raise ValueError("Image has no EXPOSE — add EXPOSE in Dockerfile.")

            return BuiltImage(tag, df_hash, base, ports, skipped_build=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return await asyncio.to_thread(work)


@dataclass
class StartedSession:
    container_id:   str
    container_name: str
    ports:          dict[str, str]   # "80/tcp" -> "32145"


async def start_session_container(
    *,
    challenge_id:  str,
    user_id:       str,
    username:      str,
    image_tag:     str,
    exposed_ports: list[str],
    cpu_limit:     float,
    memory_limit:  str,
    pids_limit:    int,
    network:       str,
) -> StartedSession:
    """Spin up a dedicated container for one player.

    Each player gets their own isolated instance — critical for pwn / exploit
    challenges where state from one player must not leak to another.
    """
    def work() -> StartedSession:
        cli = _client()

        # Auto-publish: passing None lets Docker pick a free host port per
        # exposed container port. We read back the assigned host port from
        # the container after `run()`.
        port_bindings: dict[str, None] = {p: None for p in exposed_ports}

        # Container name has to be DNS-safe and ≤63 chars.
        suffix = uuid.uuid4().hex[:10]
        chal_short = (challenge_id or "x").replace("-", "")[:8]
        user_short = (user_id      or "x").replace("-", "")[:8]
        name       = f"sor_chal_{chal_short}_{user_short}_{suffix}"[:63]

        labels = {
            LABEL_MANAGED:      "true",
            LABEL_KIND:         "challenge-session",
            LABEL_CHALLENGE_ID: str(challenge_id),
            LABEL_USER_ID:      str(user_id),
            LABEL_USERNAME:     username or "",
        }

        try:
            container = cli.containers.run(
                image=image_tag,
                name=name,
                ports=port_bindings,
                labels=labels,
                detach=True,
                # Resource caps — kept conservative for shared hosts.
                cpu_period=100_000,
                cpu_quota=int(float(cpu_limit) * 100_000),
                mem_limit=memory_limit,
                memswap_limit=memory_limit,
                pids_limit=int(pids_limit),
                # Hardening — minimum capabilities, no privilege escalation.
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],
                cap_add=["SETUID", "SETGID"],
                network=network,
                read_only=False,
                init=True,          # tini as PID 1: forwards SIGTERM, fast stop
                tmpfs={"/tmp": "size=64m"},
                restart_policy={"Name": "no"},
            )
        except APIError as exc:
            raise ValueError(f"Container start failed: {exc}")

        # Re-read so NetworkSettings.Ports is populated with the assigned
        # host ports.
        container.reload()
        port_map: dict[str, str] = {}
        ns = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        for cport, bindings in ns.items():
            if bindings:
                port_map[cport] = bindings[0].get("HostPort", "")

        return StartedSession(
            container_id=container.id,
            container_name=name,
            ports=port_map,
        )

    return await asyncio.to_thread(work)


async def stop_session_container(container_id: str) -> bool:
    """Best-effort: stop and remove. Returns True if the container existed."""
    if not container_id:
        return False

    def work() -> bool:
        cli = _client()
        try:
            c = cli.containers.get(container_id)
        except NotFound:
            return False
        try:
            c.stop(timeout=10)
        except APIError:
            pass
        try:
            c.remove(force=True)
        except APIError:
            pass
        return True

    return await asyncio.to_thread(work)


async def remove_image(image_tag: str) -> bool:
    """Best-effort image cleanup (used when a docker challenge is deleted)."""
    if not image_tag:
        return False

    def work() -> bool:
        cli = _client()
        try:
            cli.images.remove(image_tag, force=True)
            return True
        except (ImageNotFound, APIError):
            return False

    return await asyncio.to_thread(work)


async def stream_build_from_archive(
    archive_path: str,
    *,
    challenge_id:        str,
    allowed_base_images: list[str],
):
    """Async generator that yields the same `BuiltImage` semantics as
    `build_image_from_archive`, but with raw Docker build chunks interleaved
    so the caller can stream a live console to the admin UI.

    Yields dicts of one of these shapes:
      {"phase": "extracting",  "message": "..."}
      {"phase": "validating",  "message": "..."}
      {"phase": "building",    "stream": "Step 1/3 : FROM alpine\\n"}
      {"phase": "building",    "error":  "..."}             # build failure
      {"phase": "complete",    "result": BuiltImage(...)}    # success
      {"phase": "error",       "message": "..."}             # validation / other
    """
    import asyncio
    import json

    loop  = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()

    def runner() -> None:
        tmp = Path(tempfile.mkdtemp(prefix="sor_build_"))
        try:
            def emit(item):
                loop.call_soon_threadsafe(queue.put_nowait, item)

            ctx = tmp / "ctx"
            ctx.mkdir()

            emit({"phase": "extracting", "message": "Reading archive…"})

            if _is_docker_save_tar(archive_path):
                emit({"phase": "error",
                      "message": "Archive looks like 'docker save' output. "
                                 "Upload a build-context tar instead."})
                return

            _safe_extract_tar(archive_path, str(ctx))
            _sanitize_ctx(str(ctx))
            emit({"phase": "extracting", "message": "Build context ready."})

            df_path = _find_dockerfile(str(ctx))
            if not df_path:
                emit({"phase": "error", "message": "No Dockerfile found in archive."})
                return

            df_text = _read_dockerfile(df_path)
            base    = _parse_base_image(df_text)
            ports   = _parse_exposed_ports(df_text)
            emit({"phase": "validating",
                  "message": f"Dockerfile OK · base={base} · exposes={ports or '(none)'}"})

            if allowed_base_images and "*" not in allowed_base_images:
                if base not in allowed_base_images:
                    emit({"phase": "error",
                          "message": (f"Base image '{base}' not allowed. "
                                      f"Allowed: {', '.join(allowed_base_images)}")})
                    return

            df_hash = hashlib.sha256(df_text.encode()).hexdigest()
            tag     = f"sor/chal/{challenge_id}:{df_hash[:12]}"

            cli = _client()

            # Dedup — skip rebuild if the same hash is already on the daemon.
            try:
                cli.images.get(tag)
                exposed = ports or _image_exposed(cli, tag)
                if not exposed:
                    emit({"phase": "error",
                          "message": "Image has no EXPOSE — add EXPOSE in Dockerfile."})
                    return
                emit({"phase": "building",
                      "stream": f"Image already built — reusing {tag}\n"})
                result = BuiltImage(tag, df_hash, base, exposed, skipped_build=True)
                emit({"phase": "complete", "result": result})
                return
            except ImageNotFound:
                pass

            emit({"phase": "building",
                  "stream": f"Starting build → {tag}\n"})

            # Low-level API yields decoded JSON chunks — that's what we want
            # to stream to the admin console line-by-line.
            try:
                api = cli.api
                for chunk in api.build(
                    path=str(ctx),
                    tag=tag,
                    rm=True,
                    forcerm=True,
                    pull=False,
                    decode=True,
                    labels={
                        LABEL_MANAGED:      "true",
                        LABEL_KIND:         "challenge-image",
                        LABEL_CHALLENGE_ID: str(challenge_id),
                    },
                ):
                    if not isinstance(chunk, dict):
                        # Some clients send raw bytes; keep them as a stream line.
                        emit({"phase": "building", "stream": str(chunk)})
                        continue
                    if "stream" in chunk:
                        emit({"phase": "building", "stream": chunk["stream"]})
                    elif "error" in chunk:
                        emit({"phase": "building", "error": chunk["error"]})
                        emit({"phase": "error", "message": chunk["error"]})
                        return
                    elif "status" in chunk:
                        # Image-pull progress style chunks — flatten to a line.
                        emit({"phase": "building",
                              "stream": json.dumps(chunk) + "\n"})
            except (APIError, BuildError) as exc:
                emit({"phase": "error", "message": f"Docker build failed: {exc}"})
                return

            if not ports:
                ports = _image_exposed(cli, tag)
            if not ports:
                emit({"phase": "error",
                      "message": "Image has no EXPOSE — add EXPOSE in Dockerfile."})
                return

            result = BuiltImage(tag, df_hash, base, ports, skipped_build=False)
            emit({"phase": "complete", "result": result})
        except Exception as exc:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"phase": "error", "message": str(exc)},
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            loop.call_soon_threadsafe(queue.put_nowait, sentinel)

    loop.run_in_executor(None, runner)

    while True:
        item = await queue.get()
        if item is sentinel:
            break
        yield item


async def list_sor_session_containers() -> list[dict]:
    """All session containers managed by SOR (filters by label).

    Used by the admin status panel and by the cleanup loop's reconciliation.
    """
    def work() -> list[dict]:
        cli = _client()
        containers = cli.containers.list(
            all=True,
            filters={"label": f"{LABEL_MANAGED}=true"},
        )
        out: list[dict] = []
        for c in containers:
            labels = c.labels or {}
            if labels.get(LABEL_KIND) != "challenge-session":
                continue
            ns = c.attrs.get("NetworkSettings", {}).get("Ports") or {}
            ports = {k: (v[0]["HostPort"] if v else "") for k, v in ns.items()}
            out.append({
                "container_id":   c.id,
                "container_name": c.name,
                "challenge_id":   labels.get(LABEL_CHALLENGE_ID, ""),
                "user_id":        labels.get(LABEL_USER_ID, ""),
                "username":       labels.get(LABEL_USERNAME, ""),
                "status":         c.status,
                "image":          (c.image.tags[0] if c.image and c.image.tags else ""),
                "ports":          ports,
            })
        return out

    return await asyncio.to_thread(work)
