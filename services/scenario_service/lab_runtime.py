from __future__ import annotations

import asyncio
import base64
import os
import uuid
from dataclasses import dataclass, field

import docker
import yaml
from docker.errors import APIError, ImageNotFound, NotFound

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from cyber_range_core import get_logger

from docker_runtime import (
    LABEL_MANAGED, LABEL_KIND, LABEL_CHALLENGE_ID, LABEL_USER_ID, LABEL_USERNAME,
)


log = get_logger(__name__)

WG_IMAGE          = "sor/wireguard:latest"
LABEL_LAB_SESSION = "sor.lab_session"
# Per-challenge service cap — admins may define at most this many containers
# in a lab compose file. Override with the LAB_MAX_SERVICES env var.
MAX_SERVICES      = int(os.getenv("LAB_MAX_SERVICES", "5"))
SUBNET_LO, SUBNET_HI = 1, 254     # 10.10.1.0/24 .. 10.10.254.0/24
# Static service addresses inside each lab subnet start here: first service is
# 10.10.X.10, then .11, … — deterministic, and clear of Docker's dynamic pool
# start (.2), which the injected WireGuard container gets.
SERVICE_IP_BASE   = 10
# Capabilities an exercise designer may grant a lab service (SEC-027).
ALLOWED_LAB_CAPS  = {"NET_BIND_SERVICE", "NET_RAW", "CHOWN", "SETUID", "SETGID",
                     "DAC_OVERRIDE", "FOWNER", "KILL", "AUDIT_WRITE"}


def _client() -> docker.DockerClient:
    return docker.from_env()


# WireGuard keys (X25519, base64 — WireGuard's native key format)

def wg_genkey() -> tuple[str, str]:
    """Return (private_b64, public_b64) — a valid WireGuard keypair."""
    priv = X25519PrivateKey.generate()
    raw_priv = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    raw_pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw_priv).decode(), base64.b64encode(raw_pub).decode()


# Compose parsing (safe subset)

def _parse_ports(spec: dict) -> list[str]:
    """Collect the ports a service exposes, for display in the challenge UI.

    Reads both `expose:` (container-only) and `ports:` (compose host mappings) —
    labs don't publish host ports, but the container-side port of a "8080:80"
    mapping still tells the player which port to hit on the service's lab IP.
    Returns a sorted list of unique "port/proto" strings (e.g. "80/tcp").
    """
    found: set[str] = set()

    def add(token: str) -> None:
        token = token.strip().strip('"').strip("'")
        if not token:
            return
        proto = "tcp"
        if "/" in token:
            token, proto = token.rsplit("/", 1)
            proto = proto.strip().lower() or "tcp"
        container_port = token.split(":")[-1].strip()   # "8080:80" → "80"
        if container_port.isdigit():
            found.add(f"{container_port}/{proto}")

    for item in spec.get("expose", []) or []:
        add(str(item))
    for item in spec.get("ports", []) or []:
        if isinstance(item, dict):          # long-form {target: 80, published: 8080}
            tgt = item.get("target")
            if tgt is not None:
                add(f"{tgt}/{item.get('protocol', 'tcp')}")
        else:
            add(str(item))
    return sorted(found, key=lambda p: (int(p.split("/")[0]), p))


def parse_compose(text: str) -> list[dict]:
    """Extract the supported subset of an admin compose file.

    Supported per service: image (required), environment, command, and
    expose/ports (display hint only — the container-side port is shown to the
    player next to the service's lab IP; nothing is published on the host).
    Other keys (build, volumes, networks, depends_on, …) are ignored — labs run
    on a managed isolated network reached over the VPN. Raises ValueError on
    anything we can't run.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise ValueError("Compose file must have a top-level 'services' mapping.")

    services: list[dict] = []
    for name, spec in data["services"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"Service '{name}' is not a mapping.")
        image = spec.get("image")
        if not image:
            raise ValueError(
                f"Service '{name}' must specify an 'image'. "
                "Building images from source ('build:') is not supported for labs.")
        env = spec.get("environment")
        env_map: dict[str, str] = {}
        if isinstance(env, dict):
            env_map = {str(k): str(v) for k, v in env.items()}
        elif isinstance(env, list):
            for item in env:
                s = str(item)
                if "=" in s:
                    k, v = s.split("=", 1)
                    env_map[k] = v
        # White-team controls over the simulated users (SEC-027): the
        # exercise designer may run a service as a non-root user, give it a
        # read-only root filesystem, and grant capabilities from an allow-list.
        # Anything else (privileged, host mounts, host namespaces, devices)
        # is rejected, and every container still drops all other capabilities.
        for bad in ("privileged", "volumes", "devices", "pid", "ipc", "network_mode",
                    "userns_mode", "security_opt", "cgroup_parent"):
            if spec.get(bad) not in (None, False, [], {}):
                raise ValueError(f"Service '{name}': '{bad}' is not allowed in a lab definition.")
        caps = [str(c).upper().removeprefix("CAP_") for c in (spec.get("cap_add") or [])]
        denied = sorted(set(caps) - ALLOWED_LAB_CAPS)
        if denied:
            raise ValueError(f"Service '{name}': capabilities {', '.join(denied)} are not allowed "
                             f"(allowed: {', '.join(sorted(ALLOWED_LAB_CAPS))}).")
        services.append({
            "name":        str(name),
            "image":       str(image),
            "environment": env_map,
            "command":     spec.get("command"),
            "ports":       _parse_ports(spec),
            "user":        str(spec["user"]) if spec.get("user") not in (None, "") else None,
            "read_only":   bool(spec.get("read_only", False)),
            "cap_add":     caps,
        })

    if not services:
        raise ValueError("Compose file defines no services.")
    if len(services) > MAX_SERVICES:
        raise ValueError(
            f"Too many services ({len(services)} > {MAX_SERVICES}). "
            f"A lab challenge may define at most {MAX_SERVICES} containers.")
    return services


def validate_compose(text: str) -> int:
    """Validate at upload time. Returns the number of services."""
    return len(parse_compose(text))


# Subnet pool

def pick_subnet_index(used: set[int]) -> int:
    for x in range(SUBNET_LO, SUBNET_HI + 1):
        if x not in used:
            return x
    raise ValueError("Lab subnet pool exhausted — too many concurrent labs.")


# Image build (lazy)

def _ensure_wg_image(cli: docker.DockerClient, context_path: str) -> None:
    try:
        cli.images.get(WG_IMAGE)
        return
    except ImageNotFound:
        pass
    log.info("lab.wireguard.build", context=context_path)
    cli.images.build(
        path=context_path, tag=WG_IMAGE, rm=True, forcerm=True, pull=False,
        labels={LABEL_MANAGED: "true", LABEL_KIND: "lab-wireguard"})


# Launch / teardown

@dataclass
class LabInstance:
    network_name:      str
    subnet:            str
    subnet_index:      int
    wg_container_id:   str
    wg_port:           str
    wg_server_pubkey:  str
    client_conf:       str
    container_ids:     list[str] = field(default_factory=list)   # all incl. WG
    service_names:     list[str] = field(default_factory=list)
    # One entry per lab service: {name, ip, ports:[...]} — the endpoints the
    # player connects to over the VPN (each service has its own lab IP).
    endpoints:         list[dict] = field(default_factory=list)


async def launch_lab(
    *,
    challenge_id:  str,
    user_id:       str,
    username:      str,
    compose_yaml:  str,
    subnet_index:  int,
    wg_context:    str,
    endpoint_host: str = "localhost",
) -> LabInstance:
    services = parse_compose(compose_yaml)
    x = subnet_index
    lab_subnet  = f"10.10.{x}.0/24"
    lab_gateway = f"10.10.{x}.1"
    wg_addr     = f"10.13.{x}.1/24"     # WG server tunnel address
    wg_peer_ip  = f"10.13.{x}.2/32"     # player tunnel address
    server_priv, server_pub = wg_genkey()
    client_priv, client_pub = wg_genkey()

    def work() -> LabInstance:
        cli = _client()
        _ensure_wg_image(cli, wg_context)

        sid = uuid.uuid4().hex[:10]
        net_name = f"sor_lab_{x}_{sid}"
        base_labels = {
            LABEL_MANAGED:      "true",
            LABEL_CHALLENGE_ID: str(challenge_id),
            LABEL_USER_ID:      str(user_id),
            LABEL_USERNAME:     username or "",
            LABEL_LAB_SESSION:  sid,
        }

        ipam = docker.types.IPAMConfig(
            pool_configs=[docker.types.IPAMPool(subnet=lab_subnet, gateway=lab_gateway)])
        network = cli.networks.create(
            net_name, driver="bridge", ipam=ipam, check_duplicate=True,
            labels={**base_labels, LABEL_KIND: "lab-net"})

        container_ids: list[str] = []
        service_names: list[str] = []
        endpoints:     list[dict] = []
        try:
            api = cli.api
            for i, svc in enumerate(services):
                image = svc["image"]
                try:
                    cli.images.get(image)
                except ImageNotFound:
                    log.info("lab.image.pull", image=image)
                    cli.images.pull(image)

                # Deterministic static IP per service: 10.10.X.10, .11, …
                svc_ip = f"10.10.{x}.{SERVICE_IP_BASE + i}"
                cname = f"sor_lab_{x}_{svc['name']}_{sid}"[:63]
                host_cfg = api.create_host_config(
                    cap_drop=["ALL"],
                    cap_add=svc.get("cap_add") or None,
                    read_only=svc.get("read_only", False),
                    init=True,          # tini as PID 1 → SIGTERM honoured, fast teardown
                    tmpfs={"/tmp": "size=64m"} if svc.get("read_only") else None,
                    security_opt=["no-new-privileges"],
                    mem_limit="512m",
                    pids_limit=512,
                    cpu_period=100_000,
                    cpu_quota=100_000,
                    restart_policy={"Name": "no"},
                )
                # Network alias = service name so services resolve each other by
                # their compose name; static IPv4 so the player gets a stable,
                # per-service endpoint over the VPN.
                net_cfg = api.create_networking_config(
                    {net_name: api.create_endpoint_config(
                        aliases=[svc["name"]], ipv4_address=svc_ip)})
                resp = api.create_container(
                    image=image, name=cname, detach=True,
                    user=svc.get("user") or None,
                    environment=svc["environment"] or None,
                    command=svc["command"],
                    labels={**base_labels, LABEL_KIND: "lab-service"},
                    host_config=host_cfg, networking_config=net_cfg,
                    hostname=svc["name"])
                cid = resp["Id"]
                api.start(cid)
                container_ids.append(cid)
                service_names.append(svc["name"])
                endpoints.append({
                    "name":  svc["name"],
                    "ip":    svc_ip,
                    "ports": svc.get("ports") or [],
                })

            # Inject the WireGuard server on the same network with a published
            # ephemeral host UDP port.
            wg = cli.containers.run(
                image=WG_IMAGE,
                name=f"sor_lab_{x}_wg_{sid}"[:63],
                detach=True,
                network=net_name,
                ports={"51820/udp": None},
                cap_add=["NET_ADMIN"],
                sysctls={"net.ipv4.ip_forward": "1",
                         "net.ipv4.conf.all.src_valid_mark": "1"},
                environment={
                    "WG_ADDRESS":        wg_addr,
                    "WG_SERVER_PRIVKEY": server_priv,
                    "WG_PEER_PUBKEY":    client_pub,
                    "WG_PEER_IP":        wg_peer_ip,
                    "WG_LISTEN_PORT":    "51820",
                },
                labels={**base_labels, LABEL_KIND: "lab-wireguard"},
                restart_policy={"Name": "no"},
            )
            container_ids.append(wg.id)

            wg.reload()
            wg_port = ""
            ns = wg.attrs.get("NetworkSettings", {}).get("Ports") or {}
            for cport, bindings in ns.items():
                if cport.startswith("51820") and bindings:
                    wg_port = bindings[0].get("HostPort", "")
                    break

            client_conf = (
                "[Interface]\n"
                f"PrivateKey = {client_priv}\n"
                f"Address = 10.13.{x}.2/32\n\n"
                "[Peer]\n"
                f"PublicKey = {server_pub}\n"
                f"Endpoint = {endpoint_host}:{wg_port}\n"
                f"AllowedIPs = {lab_subnet}\n"
                "PersistentKeepalive = 25\n"
            )

            return LabInstance(
                network_name=net_name, subnet=lab_subnet, subnet_index=x,
                wg_container_id=wg.id, wg_port=wg_port, wg_server_pubkey=server_pub,
                client_conf=client_conf, container_ids=container_ids,
                service_names=service_names, endpoints=endpoints)
        except Exception:
            # Roll back anything we started so a failed launch leaks nothing.
            for cid in container_ids:
                try:
                    c = cli.containers.get(cid)
                    c.remove(force=True)
                except Exception:
                    pass
            try:
                network.remove()
            except Exception:
                pass
            raise

    return await asyncio.to_thread(work)


async def teardown_lab(container_ids: list[str], network_name: str) -> None:
    """Stop+remove every lab container (in parallel) and remove the network.
    Best-effort and idempotent; the subnet returns to the pool once this
    instance is gone."""
    from concurrent.futures import ThreadPoolExecutor

    def stop_one(cid: str) -> None:
        cli = _client()
        try:
            c = cli.containers.get(cid)
        except NotFound:
            return
        try:
            c.stop(timeout=5)
        except APIError:
            pass
        try:
            c.remove(force=True)
        except APIError:
            pass

    def work() -> None:
        ids = [c for c in (container_ids or []) if c]
        if ids:
            with ThreadPoolExecutor(max_workers=min(8, len(ids))) as ex:
                list(ex.map(stop_one, ids))
        if network_name:
            try:
                _client().networks.get(network_name).remove()
            except NotFound:
                pass
            except APIError as exc:
                log.warning("lab.network.remove_failed", network=network_name, error=str(exc))

    await asyncio.to_thread(work)
