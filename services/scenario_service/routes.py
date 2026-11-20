from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import aiofiles
from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request,
    UploadFile, status,
)
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import delete, select

from cyber_range_core import UserContext, get_logger

import docker_runtime
import lab_runtime
import schemas
from db import (
    Challenge, CtfEvent, CtfEventChallenge, DockerSession, DockerSpec,
    LabSpec, Submission, get_sessionmaker,
)
from security import (
    encrypt_flag,
    decrypt_flag,
    hash_flag,
    flag_matches,
    require_auth_local,
    require_role,
)


DIFFICULTIES   = {"easy", "medium", "hard"}
EDITOR_ROLES   = ("admin", "instructor")
DOCKER_CATEGORY = "docker"
CHALLENGE_TYPES = {"static", "docker", "lab"}

# Build context for the lazily-built WireGuard image (bundled with the service).
WG_CONTEXT = os.path.join(os.path.dirname(__file__), "wireguard")


def _is_docker_challenge(category: str | None, challenge_type: str | None) -> bool:
    """A challenge is docker-flavoured if either flag says so. We accept the
    legacy `category="docker"` for backward compat, but new uploads should
    pass challenge_type="docker"."""
    ct = (challenge_type or "").strip().lower()
    if ct == "docker":
        return True
    if ct == "static":
        return False
    return (category or "").strip().lower() == DOCKER_CATEGORY


log    = get_logger(__name__)
router = APIRouter()


# Helpers

def _files_dir(request: Request) -> Path:
    return Path(request.app.state.settings.CHALLENGE_FILES_DIR)


def _validate_difficulty(difficulty: str) -> str:
    d = (difficulty or "easy").lower()
    if d not in DIFFICULTIES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"difficulty must be one of {sorted(DIFFICULTIES)}",
        )
    return d


def _parse_tags(raw: str | None) -> list[str]:
    """Comma-separated string → trimmed, de-duped list."""
    if not raw:
        return []
    seen = []
    for piece in raw.split(","):
        t = piece.strip()
        if t and t not in seen:
            seen.append(t)
    return seen


async def _save_upload(file: UploadFile, dest_dir: Path) -> tuple[str, str]:
    """Stream the upload to disk. Returns (absolute_path, original_name)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_suffix = Path(file.filename or "challenge.bin").suffix
    stored_name = f"{uuid.uuid4()}{safe_suffix}"
    abs_path    = dest_dir / stored_name

    async with aiofiles.open(abs_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            await out.write(chunk)
    return str(abs_path), file.filename or stored_name


def _lab_service_count(lab: "LabSpec | None") -> int:
    if lab is None:
        return 0
    try:
        return lab_runtime.validate_compose(lab.compose_yaml)
    except Exception:
        return 0


def _full(c: Challenge, spec: DockerSpec | None = None,
          lab: "LabSpec | None" = None) -> schemas.ChallengeFull:
    return schemas.ChallengeFull(
        id=c.id, title=c.title, description=c.description,
        category=c.category,
        challenge_type=c.challenge_type or ("docker" if spec else "static"),
        difficulty=c.difficulty, points=c.points,
        flag_hash=c.flag_hash, file_name=c.file_name, author=c.author,
        max_attempts=c.max_attempts, tags=list(c.tags or []),
        created_at=c.created_at,
        visible=bool(c.visible),
        is_docker=spec is not None,
        is_lab=lab is not None,
        lab_services=_lab_service_count(lab),
        docker=_docker_spec_out(spec) if spec else None,
    )


def _public(c: Challenge, spec: DockerSpec | None = None,
            lab: "LabSpec | None" = None) -> schemas.ChallengePublic:
    return schemas.ChallengePublic(
        id=c.id, title=c.title, description=c.description,
        category=c.category,
        challenge_type=c.challenge_type or ("docker" if spec else "static"),
        difficulty=c.difficulty, points=c.points,
        file_name=c.file_name, tags=list(c.tags or []),
        max_attempts=c.max_attempts,
        is_docker=spec is not None,
        is_lab=lab is not None,
        lab_services=_lab_service_count(lab),
        docker_ports=list(spec.exposed_ports or []) if spec else [],
    )


def _docker_spec_out(spec: DockerSpec) -> schemas.DockerSpecOut:
    return schemas.DockerSpecOut(
        image_tag=spec.image_tag,
        base_image=spec.base_image,
        dockerfile_hash=spec.dockerfile_hash,
        exposed_ports=list(spec.exposed_ports or []),
        cpu_limit=spec.cpu_limit,
        memory_limit=spec.memory_limit,
        pids_limit=spec.pids_limit,
        session_ttl_minutes=spec.session_ttl_minutes,
        max_instances_per_player=spec.max_instances_per_player,
    )


def _allowed_base_images(request: Request) -> list[str]:
    raw = (request.app.state.settings.DOCKER_ALLOWED_BASE_IMAGES or "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


def _player_hostname(request: Request) -> str:
    return request.app.state.settings.PLAYER_SESSION_HOSTNAME or "localhost"


def _player_network(request: Request) -> str:
    return request.app.state.settings.DOCKER_PLAYER_NETWORK or "sor_chal_net"


def _access_urls(hostname: str, ports: dict[str, str]) -> list[str]:
    urls: list[str] = []
    for cport, hport in (ports or {}).items():
        if not hport:
            continue
        scheme = "http" if cport.split("/", 1)[0] in {"80", "8000", "8080"} else "tcp"
        if scheme == "http":
            urls.append(f"http://{hostname}:{hport}")
        else:
            urls.append(f"{hostname}:{hport}  (container {cport})")
    return urls


def _session_out(s: DockerSession) -> schemas.DockerSessionOut:
    kind = getattr(s, "kind", "single") or "single"
    # Labs are reached over the VPN, not a host http port — no access_urls.
    urls = [] if kind == "lab" else _access_urls(s.hostname, dict(s.ports or {}))
    return schemas.DockerSessionOut(
        id=s.id,
        challenge_id=s.challenge_id,
        user_id=s.user_id,
        username=s.username,
        status=s.status,
        container_id=s.container_id,
        container_name=s.container_name,
        hostname=s.hostname,
        ports=dict(s.ports or {}),
        access_urls=urls,
        expires_at=s.expires_at,
        created_at=s.created_at,
        stopped_at=s.stopped_at,
        error_message=s.error_message or "",
        kind=kind,
        subnet=getattr(s, "subnet", "") or "",
        lab_endpoints=list(getattr(s, "lab_endpoints", None) or []),
    )


async def _save_archive_to_temp(file: UploadFile) -> str:
    """Stream a tar upload to a private temp file the build code can read."""
    suffix = Path(file.filename or "ctx.tar").suffix or ".tar"
    fd, tmp_path = _mkstemp(suffix=suffix, prefix="sor_archive_")
    os.close(fd)
    async with aiofiles.open(tmp_path, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            await out.write(chunk)
    return tmp_path


def _mkstemp(*, suffix: str, prefix: str) -> tuple[int, str]:
    import tempfile
    return tempfile.mkstemp(suffix=suffix, prefix=prefix)


def _safe_unlink(path: str | None) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


async def _get_docker_spec(s, challenge_id: str) -> DockerSpec | None:
    return (await s.execute(
        select(DockerSpec).where(DockerSpec.challenge_id == challenge_id)
    )).scalar_one_or_none()


async def _get_lab_spec(s, challenge_id: str) -> LabSpec | None:
    return (await s.execute(
        select(LabSpec).where(LabSpec.challenge_id == challenge_id)
    )).scalar_one_or_none()


# CTF event helpers

def _utc(dt: datetime) -> datetime:
    """Normalise to aware-UTC (clients may send naive ISO timestamps)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _event_status(e: CtfEvent, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now < _utc(e.starts_at):
        return "upcoming"
    if now > _utc(e.ends_at):
        return "ended"
    return "active"


def _is_staff(user: UserContext) -> bool:
    return any(r in user.roles for r in EDITOR_ROLES)


async def _event_challenge_ids(s, event_id: str) -> list[str]:
    rows = (await s.execute(
        select(CtfEventChallenge.challenge_id)
        .where(CtfEventChallenge.event_id == event_id)
        .order_by(CtfEventChallenge.added_at)
    )).all()
    return [r[0] for r in rows]


async def _event_bound_challenge_ids(s) -> set[str]:
    """Every challenge id assigned to at least one event — these are hidden
    from the players' playground and gated by their events' time windows."""
    rows = (await s.execute(select(CtfEventChallenge.challenge_id).distinct())).all()
    return {r[0] for r in rows}


async def _assert_challenge_accessible(s, challenge_id: str, user: UserContext) -> None:
    """Playground challenges (no event membership) are always playable.
    Event challenges are playable by players only while one of their events
    is live. Staff bypass the gate."""
    if _is_staff(user):
        return
    event_ids = [r[0] for r in (await s.execute(
        select(CtfEventChallenge.event_id)
        .where(CtfEventChallenge.challenge_id == challenge_id)
    )).all()]
    if not event_ids:
        return
    events = (await s.execute(
        select(CtfEvent).where(CtfEvent.id.in_(event_ids))
    )).scalars().all()
    now = datetime.now(timezone.utc)
    if not any(_event_status(e, now) == "active" for e in events):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This challenge belongs to an event and is only available while "
            "the event is running.",
        )


def _event_out(e: CtfEvent, challenge_ids: list[str], *, staff: bool) -> schemas.EventOut:
    return schemas.EventOut(
        id=e.id, name=e.name, description=e.description,
        starts_at=_utc(e.starts_at), ends_at=_utc(e.ends_at),
        status=_event_status(e), author=e.author, created_at=e.created_at,
        challenge_count=len(challenge_ids),
        challenge_ids=challenge_ids if staff else [],
    )


# Admin / Instructor

@router.post("/challenges",
             status_code=status.HTTP_201_CREATED,
             response_model=schemas.ChallengeFull,
             summary="Upload a new challenge (admin / instructor)")
async def create_challenge(
    request:        Request,
    title:          Annotated[str, Form()],
    flag:           Annotated[str, Form()],
    description:    Annotated[str, Form()] = "",
    difficulty:     Annotated[str, Form()] = "easy",
    category:       Annotated[str, Form()] = "misc",
    challenge_type: Annotated[str, Form()] = "static",
    points:         Annotated[int, Form()] = 100,
    tags:           Annotated[str, Form()] = "",
    max_attempts:   Annotated[int, Form()] = 0,
    visible:        Annotated[bool, Form()] = True,
    # Docker-specific (only consulted when challenge_type == "docker"):
    docker_cpu_limit:                Annotated[float, Form()] = 1.0,
    docker_memory_limit:             Annotated[str,   Form()] = "512m",
    docker_pids_limit:               Annotated[int,   Form()] = 200,
    docker_session_ttl_minutes:      Annotated[int,   Form()] = 120,
    docker_max_instances_per_player: Annotated[int,   Form()] = 1,
    # Static: `file` is the downloadable artefact.
    # Docker: `file` is the build-context tar, `extra_file` is an optional
    # downloadable artefact for players (sources, hints, sample input, …).
    file:           Annotated[UploadFile | None, File()] = None,
    extra_file:     Annotated[UploadFile | None, File()] = None,
    user:           UserContext = Depends(require_role(*EDITOR_ROLES)),
):
    ct = (challenge_type or "static").strip().lower()
    if ct not in CHALLENGE_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"challenge_type must be one of {sorted(CHALLENGE_TYPES)}",
        )

    # Lab challenge: `file` is a Docker Compose file, stored verbatim;
    # `extra_file` is an optional downloadable artefact for players
    if ct == "lab":
        if file is None or not file.filename:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Lab challenges require a Docker Compose file uploaded as 'file'.")
        compose_text = (await file.read()).decode("utf-8", errors="replace")
        try:
            n_services = lab_runtime.validate_compose(compose_text)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid compose file: {exc}")

        lab_abs, lab_name = "", ""
        if extra_file is not None and extra_file.filename:
            lab_abs, lab_name = await _save_upload(extra_file, _files_dir(request))

        challenge = Challenge(
            title=title.strip(), description=description or "",
            category=(category or "misc").strip(), challenge_type="lab",
            difficulty=_validate_difficulty(difficulty), points=max(0, int(points)),
            flag_hash=hash_flag(flag), flag=encrypt_flag(flag),
            file_path=lab_abs, file_name=lab_name,
            author=user.username or user.id, max_attempts=max(0, int(max_attempts)),
            tags=_parse_tags(tags), visible=bool(visible),
        )
        async with get_sessionmaker()() as s:
            s.add(challenge)
            await s.commit()
            await s.refresh(challenge)
            lab = LabSpec(challenge_id=challenge.id, compose_yaml=compose_text)
            s.add(lab)
            await s.commit()
            await s.refresh(lab)

        await request.app.state.publisher.publish(
            "scenario.challenge.created",
            {"challenge_id": challenge.id, "title": challenge.title,
             "author": challenge.author, "lab": True, "services": n_services},
            correlation_id=request.state.correlation_id,
        )
        log.info("scenario.challenge.created", challenge_id=challenge.id,
                 title=challenge.title, lab=True, services=n_services)
        return _full(challenge, None, lab)

    is_docker = _is_docker_challenge(category, ct)

    if is_docker and (file is None or not file.filename):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Docker challenges require a build-context tar archive uploaded as 'file'.",
        )

    # Decide what each upload means, given the challenge type:
    #   static → `file` is the downloadable artefact, `extra_file` ignored.
    #   docker → `file` is the build-context tar (consumed at build time and
    #            deleted); `extra_file` is an optional downloadable artefact.
    abs_path, original_name = "", ""
    archive_tmp: str | None = None
    if is_docker:
        archive_tmp = await _save_archive_to_temp(file)
        if extra_file is not None and extra_file.filename:
            abs_path, original_name = await _save_upload(extra_file, _files_dir(request))
    elif file is not None and file.filename:
        abs_path, original_name = await _save_upload(file, _files_dir(request))

    challenge = Challenge(
        title=title.strip(),
        description=description or "",
        category=(category or "misc").strip(),
        challenge_type="docker" if is_docker else "static",
        difficulty=_validate_difficulty(difficulty),
        points=max(0, int(points)),
        flag_hash=hash_flag(flag),
        flag=encrypt_flag(flag),
        file_path=abs_path,
        file_name=original_name,
        author=user.username or user.id,
        max_attempts=max(0, int(max_attempts)),
        tags=_parse_tags(tags),
        visible=bool(visible),
    )

    spec: DockerSpec | None = None

    async with get_sessionmaker()() as s:
        s.add(challenge)
        await s.commit()
        await s.refresh(challenge)

        if is_docker:
            try:
                built = await docker_runtime.build_image_from_archive(
                    archive_tmp or "",
                    challenge_id=challenge.id,
                    allowed_base_images=_allowed_base_images(request),
                )
            except Exception as exc:
                # Roll back the challenge row so we don't leave orphaned
                # metadata for an image that never built.
                await s.delete(challenge)
                await s.commit()
                _safe_unlink(archive_tmp)
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

            spec = DockerSpec(
                challenge_id=challenge.id,
                image_tag=built.image_tag,
                base_image=built.base_image,
                dockerfile_hash=built.dockerfile_hash,
                exposed_ports=list(built.exposed_ports),
                cpu_limit=float(docker_cpu_limit),
                memory_limit=str(docker_memory_limit),
                pids_limit=max(50, int(docker_pids_limit)),
                session_ttl_minutes=max(1, int(docker_session_ttl_minutes)),
                max_instances_per_player=max(1, int(docker_max_instances_per_player)),
            )
            s.add(spec)
            await s.commit()
            await s.refresh(spec)

    _safe_unlink(archive_tmp)

    await request.app.state.publisher.publish(
        "scenario.challenge.created",
        {"challenge_id": challenge.id, "title": challenge.title,
         "author": challenge.author, "docker": is_docker,
         "image_tag": spec.image_tag if spec else None},
        correlation_id=request.state.correlation_id,
    )
    log.info("scenario.challenge.created",
             challenge_id=challenge.id, title=challenge.title,
             docker=is_docker,
             image_tag=spec.image_tag if spec else None)

    return _full(challenge, spec)


@router.post("/challenges/stream",
             summary="Create a challenge with a streaming build console (admin / instructor)")
async def create_challenge_stream(
    request:        Request,
    title:          Annotated[str, Form()],
    flag:           Annotated[str, Form()],
    description:    Annotated[str, Form()] = "",
    difficulty:     Annotated[str, Form()] = "easy",
    category:       Annotated[str, Form()] = "misc",
    challenge_type: Annotated[str, Form()] = "docker",
    points:         Annotated[int, Form()] = 100,
    tags:           Annotated[str, Form()] = "",
    max_attempts:   Annotated[int, Form()] = 0,
    visible:        Annotated[bool, Form()] = True,
    docker_cpu_limit:                Annotated[float, Form()] = 1.0,
    docker_memory_limit:             Annotated[str,   Form()] = "512m",
    docker_pids_limit:               Annotated[int,   Form()] = 200,
    docker_session_ttl_minutes:      Annotated[int,   Form()] = 120,
    docker_max_instances_per_player: Annotated[int,   Form()] = 1,
    file:           Annotated[UploadFile | None, File()] = None,
    extra_file:     Annotated[UploadFile | None, File()] = None,
    user:           UserContext = Depends(require_role(*EDITOR_ROLES)),
) -> StreamingResponse:
    """Identical contract to `POST /challenges` for docker challenges, but
    streams ndjson lines describing each build step so the admin UI can show
    a live console. One line per event; final line carries either the
    serialised challenge (success) or an error string."""
    import json as _json

    ct = (challenge_type or "docker").strip().lower()
    is_docker = _is_docker_challenge(category, ct)

    if not is_docker:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Streaming build is only available for docker challenges. "
            "Use POST /challenges for static challenges.",
        )
    if file is None or not file.filename:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Docker challenges require a build-context tar uploaded as 'file'.",
        )

    archive_tmp = await _save_archive_to_temp(file)
    extra_abs, extra_name = "", ""
    if extra_file is not None and extra_file.filename:
        extra_abs, extra_name = await _save_upload(extra_file, _files_dir(request))

    publisher = request.app.state.publisher
    correlation = request.state.correlation_id
    author = user.username or user.id

    async def stream():
        try:
            yield _json.dumps({"phase": "starting",
                               "message": "Build queued."}) + "\n"

            built = None
            async for ev in docker_runtime.stream_build_from_archive(
                archive_tmp,
                challenge_id=str(uuid.uuid4()),  # placeholder for label only
                allowed_base_images=_allowed_base_images(request),
            ):
                if ev.get("phase") == "complete":
                    built = ev["result"]
                    # Don't surface the BuiltImage object — final summary
                    # comes after the DB write below.
                    continue
                if ev.get("phase") == "error":
                    yield _json.dumps({"phase": "error", "message": ev["message"]}) + "\n"
                    return
                # Pass-through for "extracting", "validating", "building", …
                yield _json.dumps(ev) + "\n"

            if built is None:
                yield _json.dumps({"phase": "error",
                                   "message": "Build ended without producing an image."}) + "\n"
                return

            yield _json.dumps({"phase": "saving",
                               "message": "Recording challenge in the database…"}) + "\n"

            challenge = Challenge(
                title=title.strip(),
                description=description or "",
                category=(category or "misc").strip(),
                challenge_type="docker",
                difficulty=_validate_difficulty(difficulty),
                points=max(0, int(points)),
                flag_hash=hash_flag(flag),
                flag=encrypt_flag(flag),
                file_path=extra_abs,
                file_name=extra_name,
                author=author,
                visible=bool(visible),
                max_attempts=max(0, int(max_attempts)),
                tags=_parse_tags(tags),
            )

            async with get_sessionmaker()() as s:
                s.add(challenge)
                await s.commit()
                await s.refresh(challenge)

                # Re-tag the image to use the real challenge UUID so it
                # matches the deterministic naming used elsewhere.
                real_tag = f"sor/chal/{challenge.id}:{built.dockerfile_hash[:12]}"
                if built.image_tag != real_tag:
                    try:
                        from docker import from_env  # local import, only on this path
                        cli = from_env()
                        img = cli.images.get(built.image_tag)
                        img.tag(real_tag)
                        cli.images.remove(built.image_tag, force=False)
                    except Exception:
                        # Non-fatal — keep the original tag.
                        real_tag = built.image_tag

                spec = DockerSpec(
                    challenge_id=challenge.id,
                    image_tag=real_tag,
                    base_image=built.base_image,
                    dockerfile_hash=built.dockerfile_hash,
                    exposed_ports=list(built.exposed_ports),
                    cpu_limit=float(docker_cpu_limit),
                    memory_limit=str(docker_memory_limit),
                    pids_limit=max(50, int(docker_pids_limit)),
                    session_ttl_minutes=max(1, int(docker_session_ttl_minutes)),
                    max_instances_per_player=max(1, int(docker_max_instances_per_player)),
                )
                s.add(spec)
                await s.commit()
                await s.refresh(spec)
                result = _full(challenge, spec)

            await publisher.publish(
                "scenario.challenge.created",
                {"challenge_id": challenge.id, "title": challenge.title,
                 "author": author, "docker": True,
                 "image_tag": spec.image_tag},
                correlation_id=correlation,
            )
            log.info("scenario.challenge.created",
                     challenge_id=challenge.id, title=challenge.title,
                     docker=True, image_tag=spec.image_tag)

            yield _json.dumps({"phase": "complete",
                               "challenge": result.model_dump(mode="json")}) + "\n"

        except Exception as exc:
            yield _json.dumps({"phase": "error", "message": str(exc)}) + "\n"
        finally:
            _safe_unlink(archive_tmp)

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.put("/challenges/{challenge_id}",
            response_model=schemas.ChallengeFull,
            summary="Edit a challenge (admin / instructor)")
async def update_challenge(
    challenge_id:   str,
    request:        Request,
    title:          Annotated[str | None, Form()] = None,
    description:    Annotated[str | None, Form()] = None,
    flag:           Annotated[str | None, Form()] = None,
    difficulty:     Annotated[str | None, Form()] = None,
    category:       Annotated[str | None, Form()] = None,
    challenge_type: Annotated[str | None, Form()] = None,
    points:         Annotated[int | None, Form()] = None,
    tags:           Annotated[str | None, Form()] = None,
    max_attempts:   Annotated[int | None, Form()] = None,
    visible:        Annotated[bool | None, Form()] = None,
    docker_cpu_limit:                Annotated[float | None, Form()] = None,
    docker_memory_limit:             Annotated[str   | None, Form()] = None,
    docker_pids_limit:               Annotated[int   | None, Form()] = None,
    docker_session_ttl_minutes:      Annotated[int   | None, Form()] = None,
    docker_max_instances_per_player: Annotated[int   | None, Form()] = None,
    file:           Annotated[UploadFile | None, File()] = None,
    extra_file:     Annotated[UploadFile | None, File()] = None,
    user:           UserContext = Depends(require_role(*EDITOR_ROLES)),
):
    archive_tmp: str | None = None
    async with get_sessionmaker()() as s:
        c = await s.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found")

        spec = await _get_docker_spec(s, challenge_id)

        if title        is not None: c.title       = title.strip()
        if description  is not None: c.description = description
        if difficulty   is not None: c.difficulty  = _validate_difficulty(difficulty)
        if category     is not None: c.category    = category.strip() or "misc"
        if points       is not None: c.points      = max(0, int(points))
        if max_attempts is not None: c.max_attempts = max(0, int(max_attempts))
        if tags         is not None: c.tags        = _parse_tags(tags)
        if visible      is not None: c.visible     = bool(visible)
        if flag         is not None and flag.strip():
            c.flag_hash = hash_flag(flag)
            c.flag      = encrypt_flag(flag)

        # Lab challenges: optionally replace the compose file (`file`) and/or
        # the downloadable artefact (`extra_file`); no docker spec.
        if c.challenge_type == "lab":
            lab = await _get_lab_spec(s, challenge_id)
            if file is not None and file.filename:
                compose_text = (await file.read()).decode("utf-8", errors="replace")
                try:
                    lab_runtime.validate_compose(compose_text)
                except ValueError as exc:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                        f"Invalid compose file: {exc}")
                if lab is None:
                    lab = LabSpec(challenge_id=challenge_id, compose_yaml=compose_text)
                    s.add(lab)
                else:
                    lab.compose_yaml = compose_text
                    lab.rebuilt_at = datetime.now(timezone.utc)
            if extra_file is not None and extra_file.filename:
                old_path = c.file_path
                abs_path, original_name = await _save_upload(extra_file, _files_dir(request))
                c.file_path = abs_path
                c.file_name = original_name
                if old_path and os.path.exists(old_path):
                    _safe_unlink(old_path)
            await s.commit()
            await s.refresh(c)
            if lab is not None:
                await s.refresh(lab)
            log.info("scenario.challenge.updated", challenge_id=challenge_id, by=user.id)
            return _full(c, None, lab)

        if challenge_type is not None:
            ct = challenge_type.strip().lower()
            if ct not in CHALLENGE_TYPES:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"challenge_type must be one of {sorted(CHALLENGE_TYPES)}",
                )
            c.challenge_type = ct

        # docker-ness is determined by the (possibly updated) type / category.
        is_docker_now = _is_docker_challenge(c.category, c.challenge_type)

        # Tunable docker spec fields (only applied when challenge is docker).
        if spec and is_docker_now:
            if docker_cpu_limit                is not None: spec.cpu_limit                = float(docker_cpu_limit)
            if docker_memory_limit             is not None: spec.memory_limit             = str(docker_memory_limit)
            if docker_pids_limit               is not None: spec.pids_limit               = max(50, int(docker_pids_limit))
            if docker_session_ttl_minutes      is not None: spec.session_ttl_minutes      = max(1,  int(docker_session_ttl_minutes))
            if docker_max_instances_per_player is not None: spec.max_instances_per_player = max(1,  int(docker_max_instances_per_player))

        # Replacement uploads — meaning depends on type.
        #   docker: `file`       → rebuild image from new tar (optional)
        #           `extra_file` → replace the downloadable artefact (optional)
        #   static: `file`       → replace the downloadable artefact (optional)
        if file is not None and file.filename:
            if is_docker_now:
                if spec is None:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "Cannot attach a docker archive: this challenge has no docker spec. "
                        "Recreate it with challenge_type='docker'.",
                    )
                archive_tmp = await _save_archive_to_temp(file)
                try:
                    built = await docker_runtime.build_image_from_archive(
                        archive_tmp,
                        challenge_id=c.id,
                        allowed_base_images=_allowed_base_images(request),
                    )
                except Exception as exc:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

                old_image = spec.image_tag
                spec.image_tag       = built.image_tag
                spec.base_image      = built.base_image
                spec.dockerfile_hash = built.dockerfile_hash
                spec.exposed_ports   = list(built.exposed_ports)
                spec.rebuilt_at      = datetime.now(timezone.utc)

                if old_image and old_image != spec.image_tag:
                    await docker_runtime.remove_image(old_image)
            else:
                old_path = c.file_path
                abs_path, original_name = await _save_upload(file, _files_dir(request))
                c.file_path = abs_path
                c.file_name = original_name
                if old_path and os.path.exists(old_path):
                    _safe_unlink(old_path)

        # Optional replacement of the downloadable artefact on docker challenges.
        if is_docker_now and extra_file is not None and extra_file.filename:
            old_path = c.file_path
            abs_path, original_name = await _save_upload(extra_file, _files_dir(request))
            c.file_path = abs_path
            c.file_name = original_name
            if old_path and os.path.exists(old_path):
                _safe_unlink(old_path)

        await s.commit()
        await s.refresh(c)
        if spec is not None:
            await s.refresh(spec)
        result = _full(c, spec)

    _safe_unlink(archive_tmp)

    log.info("scenario.challenge.updated", challenge_id=challenge_id, by=user.id)
    return result


@router.delete("/challenges/{challenge_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a challenge and its file (admin / instructor)")
async def delete_challenge(
    challenge_id: str,
    request: Request,
    user: UserContext = Depends(require_role(*EDITOR_ROLES)),
):
    image_to_remove: str = ""
    sessions_to_stop: list[str] = []                       # single-container ids
    labs_to_teardown: list[tuple[list, str]] = []          # (container_ids, network)

    async with get_sessionmaker()() as s:
        c = await s.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found")
        file_path = c.file_path

        spec = await _get_docker_spec(s, challenge_id)
        if spec is not None:
            image_to_remove = spec.image_tag

        # Tear down every live session for this challenge — single or lab.
        sessions = (await s.execute(
            select(DockerSession).where(
                DockerSession.challenge_id == challenge_id,
                DockerSession.status.in_(("running", "creating")),
            )
        )).scalars().all()
        for s_ in sessions:
            if (getattr(s_, "kind", "single") or "single") == "lab":
                labs_to_teardown.append((list(s_.lab_container_ids or []), s_.network_name))
            elif s_.container_id:
                sessions_to_stop.append(s_.container_id)
            s_.status     = "stopped"
            s_.stopped_at = datetime.now(timezone.utc)

        # ON DELETE CASCADE drops the docker_specs / lab_specs row alongside the
        # challenge. Sessions are not foreign-keyed so we cleared them above.
        await s.delete(c)
        await s.commit()

    # Best-effort housekeeping outside the txn.
    if file_path and os.path.exists(file_path):
        _safe_unlink(file_path)
    for cids, net in labs_to_teardown:
        await lab_runtime.teardown_lab(cids, net)
    for cid in sessions_to_stop:
        await docker_runtime.stop_session_container(cid)
    if image_to_remove:
        await docker_runtime.remove_image(image_to_remove)

    await request.app.state.publisher.publish(
        "scenario.challenge.deleted",
        {"challenge_id": challenge_id, "deleted_by": user.id,
         "image_removed": image_to_remove or None,
         "sessions_stopped": len(sessions_to_stop)},
        correlation_id=request.state.correlation_id,
    )
    log.info("scenario.challenge.deleted",
             challenge_id=challenge_id, by=user.id,
             sessions_stopped=len(sessions_to_stop),
             image_removed=image_to_remove or None)
    return None


# Listing

@router.get("/challenges",
            summary="List challenges (full metadata for staff, redacted for trainees)")
async def list_challenges(user: UserContext = Depends(require_auth_local)):
    is_staff = any(r in user.roles for r in EDITOR_ROLES)
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(Challenge).order_by(Challenge.created_at))).scalars().all()
        specs = (await s.execute(select(DockerSpec))).scalars().all()
        labs  = (await s.execute(select(LabSpec))).scalars().all()
        event_bound = await _event_bound_challenge_ids(s)
    spec_by_chal = {sp.challenge_id: sp for sp in specs}
    lab_by_chal  = {lb.challenge_id: lb for lb in labs}
    out = []
    for c in rows:
        # Players never see challenges hidden by staff.
        if not is_staff and not c.visible:
            continue
        # Event challenges leave the playground — players reach them through
        # the Events tab while their event is live.
        if not is_staff and c.id in event_bound:
            continue
        if is_staff:
            out.append(_full(c, spec_by_chal.get(c.id), lab_by_chal.get(c.id)))
        else:
            out.append(_public(c, spec_by_chal.get(c.id), lab_by_chal.get(c.id)))
    return out


@router.post("/challenges/{challenge_id}/visibility",
             response_model=schemas.ChallengeFull,
             summary="Show / hide a challenge from players (admin / instructor)")
async def set_visibility(challenge_id: str,
                         body: schemas.VisibilityRequest,
                         request: Request,
                         user: UserContext = Depends(require_role(*EDITOR_ROLES))):
    async with get_sessionmaker()() as s:
        c = await s.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found")
        c.visible = bool(body.visible)
        await s.commit()
        await s.refresh(c)
        spec = await _get_docker_spec(s, challenge_id)
        lab  = await _get_lab_spec(s, challenge_id)
        result = _full(c, spec, lab)

    await request.app.state.publisher.publish(
        "scenario.challenge.visibility",
        {"challenge_id": challenge_id, "visible": c.visible, "by": user.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("scenario.challenge.visibility", challenge_id=challenge_id,
             visible=c.visible, by=user.id)
    return result


@router.get("/challenges/stats",
            response_model=schemas.ChallengeStatsOut,
            summary="Per-challenge play statistics (admin / instructor)")
async def challenge_stats(_: UserContext = Depends(require_role(*EDITOR_ROLES))):
    """Attempt / solver counts and completion rate per challenge, most-played
    first. Feeds the admin & instructor dashboards. Aggregated in-process —
    submission volume in a lab is modest."""
    async with get_sessionmaker()() as s:
        challenges = (await s.execute(select(Challenge))).scalars().all()
        subs = (await s.execute(select(Submission))).scalars().all()

    agg: dict[str, dict] = {}
    for sub in subs:
        d = agg.setdefault(sub.challenge_id,
                           {"attempts": 0, "attempters": set(), "solvers": set()})
        d["attempts"] += 1
        d["attempters"].add(sub.user_id)
        if sub.correct:
            d["solvers"].add(sub.user_id)

    rows: list[schemas.ChallengeStatRow] = []
    total_solves = 0
    for c in challenges:
        d = agg.get(c.id, {"attempts": 0, "attempters": set(), "solvers": set()})
        attempters = len(d["attempters"])
        solvers    = len(d["solvers"])
        total_solves += solvers
        rows.append(schemas.ChallengeStatRow(
            challenge_id=c.id, title=c.title, difficulty=c.difficulty,
            points=c.points, attempts=d["attempts"], attempters=attempters,
            solvers=solvers,
            completion_rate=round(solvers / attempters, 3) if attempters else 0.0,
        ))
    rows.sort(key=lambda r: (r.attempts, r.solvers), reverse=True)

    return schemas.ChallengeStatsOut(
        total_challenges=len(challenges),
        total_submissions=len(subs),
        total_solves=total_solves,
        challenges=rows,
    )


@router.get("/challenges/{challenge_id}/flag",
            response_model=schemas.FlagOut,
            summary="Reveal a challenge's plaintext flag (admin only)")
async def get_challenge_flag(
    challenge_id: str,
    _: UserContext = Depends(require_role("admin")),
):
    async with get_sessionmaker()() as s:
        c = await s.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found")
        flag = decrypt_flag(c.flag or "")
    return schemas.FlagOut(
        challenge_id=challenge_id, flag=flag, recoverable=bool(flag),
    )


# Trainee actions

@router.get("/challenges/{challenge_id}/download",
            summary="Download the challenge file")
async def download_challenge(
    challenge_id: str,
    user: UserContext = Depends(require_auth_local),
):
    async with get_sessionmaker()() as s:
        c = await s.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found")
        await _assert_challenge_accessible(s, challenge_id, user)

    # `file_path` always points to the player-facing artefact (the static
    # file for static challenges, the optional `extra_file` for docker ones).
    # The docker build context is consumed at build time and never stored.
    if not c.file_path or not os.path.exists(c.file_path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge has no file attached")
    return FileResponse(
        c.file_path,
        filename=c.file_name or "challenge.bin",
        media_type="application/octet-stream",
    )


@router.get("/my-solves",
            summary="Return the set of challenge IDs the caller has already solved")
async def my_solves(user: UserContext = Depends(require_auth_local)) -> dict:
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(Submission.challenge_id)
            .where(Submission.user_id == user.id, Submission.correct == True)  # noqa: E712
            .distinct()
        )).all()
    return {"solved": [r[0] for r in rows]}


@router.delete("/submissions",
               summary="Delete a player's correct submissions for a challenge "
                       "(admin / instructor — used when a solve is revoked)")
async def delete_correct_submissions(
    challenge_id: str,
    user_id: str,
    request: Request,
    staff: UserContext = Depends(require_role(*EDITOR_ROLES)),
) -> dict:
    async with get_sessionmaker()() as s:
        res = await s.execute(delete(Submission).where(
            Submission.challenge_id == challenge_id,
            Submission.user_id      == user_id,
            Submission.correct      == True,  # noqa: E712
        ))
        await s.commit()
    log.info("scenario.submissions.revoked", challenge_id=challenge_id,
             user_id=user_id, deleted=res.rowcount, by=staff.id)
    return {"deleted": res.rowcount}


@router.post("/challenges/{challenge_id}/submit",
             response_model=schemas.SubmissionResult,
             summary="Submit a flag for this challenge")
async def submit_flag(
    challenge_id: str,
    body: schemas.FlagSubmission,
    request: Request,
    user: UserContext = Depends(require_auth_local),
):
    async with get_sessionmaker()() as s:
        c = await s.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found")
        await _assert_challenge_accessible(s, challenge_id, user)

        # Has this user already solved this one? (No double points.)
        prev = (await s.execute(
            select(Submission).where(
                Submission.challenge_id == challenge_id,
                Submission.user_id      == user.id,
            )
        )).scalars().all()

        already_solved   = any(p.correct for p in prev)
        attempts_so_far  = len(prev)

        if c.max_attempts and attempts_so_far >= c.max_attempts and not already_solved:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"No attempts remaining (limit: {c.max_attempts})",
            )

        correct     = flag_matches(body.flag, c.flag_hash)
        first_solve = correct and not already_solved

        # Scenario Management records the attempt (for attempt limits and
        # solved-state), but it does NOT score. Scoring is the Learning &
        # Progress concern (F10): it consumes the event below and awards
        # points by difficulty. So no points are computed here.
        s.add(Submission(
            challenge_id=challenge_id,
            user_id=user.id,
            username=user.username,
            correct=correct,
            points=0,
        ))
        difficulty = c.difficulty
        await s.commit()

    # Tell Learning & Progress what happened; it decides the points.
    await request.app.state.publisher.publish(
        "scenario.flag.submitted",
        {"challenge_id": challenge_id, "user_id": user.id,
         "username": user.username, "difficulty": difficulty,
         "correct": correct, "first_solve": first_solve},
        correlation_id=request.state.correlation_id,
    )
    log.info("scenario.flag.submitted",
             challenge_id=challenge_id, user_id=user.id,
             correct=correct, first_solve=first_solve)

    if correct:
        msg = ("Correct flag — you had already solved this one."
               if already_solved else "Correct flag.")
    else:
        msg = "Incorrect flag."

    return schemas.SubmissionResult(correct=correct, message=msg)


# Lab instances (multi-container + WireGuard)

LAB_TTL_MINUTES = 180


async def _start_lab_session(request: Request, challenge_id: str, user: UserContext):
    """Launch a multi-container lab on a unique isolated network with an
    injected WireGuard server. Instance lifecycle lives entirely here."""
    # 1. Reserve the instance: enforce one running lab per player, allocate a
    #    unique subnet index, and record a 'creating' row before the slow work.
    async with get_sessionmaker()() as s:
        lab = await _get_lab_spec(s, challenge_id)
        if lab is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "This lab challenge has no compose file.")

        running = (await s.execute(
            select(DockerSession).where(
                DockerSession.challenge_id == challenge_id,
                DockerSession.user_id      == user.id,
                DockerSession.kind         == "lab",
                DockerSession.status.in_(("creating", "running")),
            )
        )).scalars().all()
        if running:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "You already have a running instance of this lab.")

        used_rows = (await s.execute(
            select(DockerSession.subnet_index).where(
                DockerSession.kind == "lab",
                DockerSession.status.in_(("creating", "running")),
                DockerSession.subnet_index != 0,
            )
        )).all()
        used = {r[0] for r in used_rows}
        try:
            index = lab_runtime.pick_subnet_index(used)
        except ValueError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

        hostname = _player_hostname(request)
        session = DockerSession(
            challenge_id=challenge_id, user_id=user.id,
            username=user.username or "", hostname=hostname,
            status="creating", kind="lab", subnet_index=index,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=LAB_TTL_MINUTES),
            ports={},
        )
        s.add(session)
        await s.commit()
        await s.refresh(session)
        session_id   = session.id
        compose_yaml = lab.compose_yaml

    # 2. Build the instance (slow: image pulls, container starts, WG up).
    try:
        inst = await lab_runtime.launch_lab(
            challenge_id=challenge_id, user_id=user.id,
            username=user.username or "", compose_yaml=compose_yaml,
            subnet_index=index, wg_context=WG_CONTEXT, endpoint_host=hostname,
        )
    except Exception as exc:
        async with get_sessionmaker()() as s:
            sess = await s.get(DockerSession, session_id)
            if sess is not None:
                sess.status        = "error"
                sess.error_message = str(exc)[:1000]
                sess.stopped_at    = datetime.now(timezone.utc)
                await s.commit()
        code = 400 if isinstance(exc, ValueError) else 500
        raise HTTPException(code, f"Lab launch failed: {exc}")

    # 3. Finalise the session row with the instance details.
    async with get_sessionmaker()() as s:
        sess = await s.get(DockerSession, session_id)
        sess.container_id      = inst.wg_container_id
        sess.container_name    = f"sor_lab_wg_{session_id[:8]}"
        sess.network_name      = inst.network_name
        sess.subnet            = inst.subnet
        sess.lab_container_ids = inst.container_ids
        sess.wg_port           = inst.wg_port
        sess.wg_server_pubkey  = inst.wg_server_pubkey
        sess.client_conf       = inst.client_conf
        sess.lab_endpoints     = inst.endpoints
        sess.ports             = {"51820/udp": inst.wg_port}
        sess.status            = "running"
        await s.commit()
        await s.refresh(sess)
        result = _session_out(sess)

    await request.app.state.publisher.publish(
        "docker.container.started",
        {"session_id": session_id, "challenge_id": challenge_id,
         "user_id": user.id, "kind": "lab", "subnet": inst.subnet,
         "services": len(inst.service_names), "wg_port": inst.wg_port},
        correlation_id=request.state.correlation_id,
    )
    log.info("lab.instance.started", session_id=session_id, challenge_id=challenge_id,
             user_id=user.id, subnet=inst.subnet, services=len(inst.service_names))
    return result


# Docker challenge sessions

@router.post("/challenges/{challenge_id}/session",
             response_model=schemas.DockerSessionOut,
             status_code=status.HTTP_201_CREATED,
             summary="Start a per-player instance (single container, or a lab)")
async def start_session(
    challenge_id: str,
    request: Request,
    user: UserContext = Depends(require_auth_local),
):
    async with get_sessionmaker()() as s:
        c = await s.get(Challenge, challenge_id)
        if c is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Challenge not found")
        await _assert_challenge_accessible(s, challenge_id, user)
        # Lab challenges branch to the multi-container + WireGuard launch path.
        if c.challenge_type == "lab":
            return await _start_lab_session(request, challenge_id, user)
        spec = await _get_docker_spec(s, challenge_id)
        if spec is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This challenge is not a docker challenge.",
            )

        # Enforce the per-player instance cap.
        running = (await s.execute(
            select(DockerSession).where(
                DockerSession.challenge_id == challenge_id,
                DockerSession.user_id      == user.id,
                DockerSession.status       == "running",
            )
        )).scalars().all()
        if len(running) >= int(spec.max_instances_per_player):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"You already have {len(running)} running session(s) for this "
                f"challenge (limit: {spec.max_instances_per_player}).",
            )

        hostname = _player_hostname(request)
        network  = _player_network(request)
        ttl_min  = int(spec.session_ttl_minutes)
        expires  = datetime.now(timezone.utc) + timedelta(minutes=ttl_min)

        session = DockerSession(
            challenge_id=challenge_id,
            user_id=user.id,
            username=user.username or "",
            hostname=hostname,
            status="creating",
            expires_at=expires,
            ports={},
        )
        s.add(session)
        await s.commit()
        await s.refresh(session)
        session_id = session.id

        try:
            started = await docker_runtime.start_session_container(
                challenge_id=challenge_id,
                user_id=user.id,
                username=user.username or "",
                image_tag=spec.image_tag,
                exposed_ports=list(spec.exposed_ports or []),
                cpu_limit=spec.cpu_limit,
                memory_limit=spec.memory_limit,
                pids_limit=spec.pids_limit,
                network=network,
            )
        except Exception as exc:
            session.status        = "error"
            session.error_message = str(exc)[:1000]
            session.stopped_at    = datetime.now(timezone.utc)
            await s.commit()
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))

        session.container_id   = started.container_id
        session.container_name = started.container_name
        session.ports          = dict(started.ports or {})
        session.status         = "running"
        await s.commit()
        await s.refresh(session)
        result = _session_out(session)

    await request.app.state.publisher.publish(
        "docker.container.started",
        {"session_id": session_id,
         "challenge_id": challenge_id,
         "user_id": user.id,
         "container_id": result.container_id,
         "container_name": result.container_name,
         "image": spec.image_tag,
         "ports": result.ports},
        correlation_id=request.state.correlation_id,
    )
    log.info("docker.container.started",
             session_id=session_id, challenge_id=challenge_id,
             user_id=user.id, container_id=result.container_id,
             ports=result.ports)
    return result


@router.get("/sessions",
            response_model=list[schemas.DockerSessionOut],
            summary="List docker challenge sessions (own for trainees, all for staff)")
async def list_sessions(user: UserContext = Depends(require_auth_local)):
    is_staff = any(r in user.roles for r in EDITOR_ROLES)
    async with get_sessionmaker()() as s:
        q = select(DockerSession).order_by(DockerSession.created_at.desc())
        if not is_staff:
            q = q.where(DockerSession.user_id == user.id)
        rows = (await s.execute(q)).scalars().all()
    return [_session_out(r) for r in rows]


@router.delete("/sessions/{session_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Stop and tear down a docker session")
async def stop_session(
    session_id: str,
    request: Request,
    user: UserContext = Depends(require_auth_local),
):
    is_staff = any(r in user.roles for r in EDITOR_ROLES)
    async with get_sessionmaker()() as s:
        sess = await s.get(DockerSession, session_id)
        if sess is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
        if not is_staff and sess.user_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")

        cid          = sess.container_id
        chal_id      = sess.challenge_id
        owner_id     = sess.user_id
        kind         = getattr(sess, "kind", "single") or "single"
        lab_cids     = list(sess.lab_container_ids or [])
        network_name = sess.network_name

        if sess.status in ("running", "creating"):
            sess.status     = "stopped"
            sess.stopped_at = datetime.now(timezone.utc)
            await s.commit()

    if kind == "lab":
        # Stop every container (incl. WireGuard) and remove the network; the
        # subnet returns to the pool automatically.
        await lab_runtime.teardown_lab(lab_cids, network_name)
    elif cid:
        await docker_runtime.stop_session_container(cid)

    await request.app.state.publisher.publish(
        "docker.container.stopped",
        {"session_id": session_id,
         "challenge_id": chal_id,
         "user_id": owner_id,
         "container_id": cid,
         "kind": kind,
         "stopped_by": user.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("docker.container.stopped",
             session_id=session_id, challenge_id=chal_id,
             user_id=owner_id, kind=kind, stopped_by=user.id)
    return None


@router.get("/sessions/{session_id}/wireguard.conf",
            summary="Download the player's WireGuard config for a lab instance")
async def download_wireguard_conf(
    session_id: str,
    user: UserContext = Depends(require_auth_local),
):
    is_staff = any(r in user.roles for r in EDITOR_ROLES)
    async with get_sessionmaker()() as s:
        sess = await s.get(DockerSession, session_id)
        if sess is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
        if not is_staff and sess.user_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")
        if (getattr(sess, "kind", "single") or "single") != "lab" or not sess.client_conf:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "No WireGuard config for this session.")
        conf = sess.client_conf

    return PlainTextResponse(
        conf,
        headers={"Content-Disposition": f'attachment; filename="sor-lab-{session_id[:8]}.conf"'},
        media_type="text/plain",
    )


@router.get("/docker/status",
            response_model=schemas.DockerStatusOut,
            summary="Live Docker daemon view of all SOR-managed session containers (admin / instructor)")
async def docker_status(_: UserContext = Depends(require_role(*EDITOR_ROLES))):
    reachable = await docker_runtime.docker_socket_reachable()
    if not reachable:
        return schemas.DockerStatusOut(daemon_reachable=False, sessions=[])
    sessions = await docker_runtime.list_sor_session_containers()
    return schemas.DockerStatusOut(daemon_reachable=True, sessions=sessions)


# CTF events (time-boxed challenge sets)
#
# The playground (`/challenges`) is the always-on set. An event groups chosen
# challenges behind a start/end window: while assigned to any event a
# challenge disappears from the players' playground and is playable only
# through the event — and only while the event is live. Staff (admin /
# instructor) create, edit and delete events and pick the included
# challenges; they bypass the time gate.

async def _validate_event_challenge_ids(s, ids: list[str]) -> list[str]:
    """De-dupe (order-preserving) and verify every id exists."""
    deduped = list(dict.fromkeys(ids or []))
    if not deduped:
        return []
    found = {r[0] for r in (await s.execute(
        select(Challenge.id).where(Challenge.id.in_(deduped))
    )).all()}
    missing = [i for i in deduped if i not in found]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown challenge id(s): {', '.join(missing)}",
        )
    return deduped


def _validate_event_window(starts_at: datetime, ends_at: datetime) -> tuple[datetime, datetime]:
    starts, ends = _utc(starts_at), _utc(ends_at)
    if ends <= starts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Event end must be after its start.")
    return starts, ends


@router.get("/events",
            response_model=list[schemas.EventOut],
            summary="List CTF events (all signed-in users)")
async def list_events(user: UserContext = Depends(require_auth_local)):
    staff = _is_staff(user)
    async with get_sessionmaker()() as s:
        events = (await s.execute(
            select(CtfEvent).order_by(CtfEvent.starts_at.desc())
        )).scalars().all()
        members = (await s.execute(select(CtfEventChallenge))).scalars().all()
    ids_by_event: dict[str, list[str]] = {}
    for m in sorted(members, key=lambda m: m.added_at):
        ids_by_event.setdefault(m.event_id, []).append(m.challenge_id)
    # Live events first, then upcoming (soonest first), then ended (latest first).
    rank = {"active": 0, "upcoming": 1, "ended": 2}

    def _sort_key(e: CtfEvent) -> tuple:
        s = _event_status(e)
        if s == "ended":
            return (rank[s], -_utc(e.ends_at).timestamp())
        return (rank[s], _utc(e.starts_at).timestamp())

    events.sort(key=_sort_key)
    return [_event_out(e, ids_by_event.get(e.id, []), staff=staff) for e in events]


@router.post("/events",
             response_model=schemas.EventDetailOut,
             status_code=status.HTTP_201_CREATED,
             summary="Create a CTF event (admin / instructor)")
async def create_event(body: schemas.EventCreate,
                       request: Request,
                       user: UserContext = Depends(require_role(*EDITOR_ROLES))):
    starts, ends = _validate_event_window(body.starts_at, body.ends_at)
    async with get_sessionmaker()() as s:
        challenge_ids = await _validate_event_challenge_ids(s, body.challenge_ids)
        e = CtfEvent(
            name=body.name.strip(), description=body.description or "",
            starts_at=starts, ends_at=ends,
            author=user.username or user.id, created_by=user.id,
        )
        s.add(e)
        await s.commit()
        await s.refresh(e)
        for cid in challenge_ids:
            s.add(CtfEventChallenge(event_id=e.id, challenge_id=cid))
        await s.commit()
        detail = await _event_detail(s, e, user)

    await request.app.state.publisher.publish(
        "scenario.event.created",
        {"event_id": e.id, "name": e.name, "starts_at": starts.isoformat(),
         "ends_at": ends.isoformat(), "challenges": len(challenge_ids),
         "by": user.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("scenario.event.created", event_id=e.id, name=e.name,
             challenges=len(challenge_ids), by=user.id)
    return detail


async def _event_detail(s, e: CtfEvent, user: UserContext) -> schemas.EventDetailOut:
    staff = _is_staff(user)
    challenge_ids = await _event_challenge_ids(s, e.id)
    accessible = staff or _event_status(e) == "active"

    challenges: list = []
    if accessible and challenge_ids:
        rows = (await s.execute(
            select(Challenge).where(Challenge.id.in_(challenge_ids))
        )).scalars().all()
        by_id = {c.id: c for c in rows}
        for cid in challenge_ids:
            c = by_id.get(cid)
            if c is None:
                continue
            if not staff and not c.visible:
                continue
            spec = await _get_docker_spec(s, cid)
            lab  = await _get_lab_spec(s, cid)
            challenges.append(_full(c, spec, lab) if staff else _public(c, spec, lab))

    base = _event_out(e, challenge_ids, staff=staff)
    return schemas.EventDetailOut(
        **base.model_dump(),
        accessible=accessible,
        challenges=challenges,
    )


@router.get("/events/{event_id}",
            response_model=schemas.EventDetailOut,
            summary="Event detail — challenges included only while playable")
async def get_event(event_id: str, user: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        e = await s.get(CtfEvent, event_id)
        if e is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
        return await _event_detail(s, e, user)


@router.put("/events/{event_id}",
            response_model=schemas.EventDetailOut,
            summary="Edit a CTF event (admin / instructor)")
async def update_event(event_id: str,
                       body: schemas.EventUpdate,
                       request: Request,
                       user: UserContext = Depends(require_role(*EDITOR_ROLES))):
    async with get_sessionmaker()() as s:
        e = await s.get(CtfEvent, event_id)
        if e is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

        if body.name is not None:
            e.name = body.name.strip()
        if body.description is not None:
            e.description = body.description
        starts = _utc(body.starts_at) if body.starts_at is not None else _utc(e.starts_at)
        ends   = _utc(body.ends_at)   if body.ends_at   is not None else _utc(e.ends_at)
        e.starts_at, e.ends_at = _validate_event_window(starts, ends)

        if body.challenge_ids is not None:
            challenge_ids = await _validate_event_challenge_ids(s, body.challenge_ids)
            await s.execute(delete(CtfEventChallenge)
                            .where(CtfEventChallenge.event_id == event_id))
            for cid in challenge_ids:
                s.add(CtfEventChallenge(event_id=event_id, challenge_id=cid))

        await s.commit()
        await s.refresh(e)
        detail = await _event_detail(s, e, user)

    await request.app.state.publisher.publish(
        "scenario.event.updated",
        {"event_id": event_id, "name": detail.name, "by": user.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("scenario.event.updated", event_id=event_id, by=user.id)
    return detail


@router.delete("/events/{event_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a CTF event (admin / instructor)")
async def delete_event(event_id: str,
                       request: Request,
                       user: UserContext = Depends(require_role(*EDITOR_ROLES))):
    async with get_sessionmaker()() as s:
        e = await s.get(CtfEvent, event_id)
        if e is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
        name = e.name
        # ON DELETE CASCADE clears ctf_event_challenges; the challenges
        # themselves return to the playground untouched.
        await s.delete(e)
        await s.commit()

    await request.app.state.publisher.publish(
        "scenario.event.deleted",
        {"event_id": event_id, "name": name, "by": user.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("scenario.event.deleted", event_id=event_id, name=name, by=user.id)
    return None
