from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import select

from cyber_range_core import CoreSettings, create_app, get_logger

import db
import docker_runtime
from db import DockerSession
from routes import router


class Settings(CoreSettings):
    SERVICE_NAME: str = "scenario-service"
    PORT: int         = 8002

    DATABASE_URL: str = "postgresql+asyncpg://sor:sor@localhost:5432/sor"

    # Where uploaded challenge files are stored on disk.
    CHALLENGE_FILES_DIR: str = "/data/challenges"

    # Token verification; the deployment sets RS256 and the IAM public key.
    JWT_SECRET:    str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER:    str = "cyber-range-iam"

    # docker challenge runtime
    # Comma-separated list of FROM-allowed base images. "*" disables the
    # whitelist (lab use only).
    DOCKER_ALLOWED_BASE_IMAGES: str = (
        "ubuntu:22.04,ubuntu:latest,alpine:latest,alpine:3.20,"
        "debian:bookworm-slim,python:3.11-slim,python:3.12-slim,"
        "nginx:1.27-alpine"
    )
    # Hostname trainees use to reach their session containers.
    PLAYER_SESSION_HOSTNAME: str = "localhost"
    # Bridge network player containers join. Created on startup if missing.
    DOCKER_PLAYER_NETWORK:   str = "sor_chal_net"
    # Fixed subnet for that network so the host egress guard can match it.
    DOCKER_PLAYER_SUBNET:    str = "172.20.0.0/16"
    # Fernet keys for flags at rest, newest first (SEC-005).
    FLAG_KEYS_FILE:          str = ""
    # (no auto-cleanup of sessions — see _on_startup)


settings = Settings()
log      = get_logger(__name__)


# Startup / shutdown

async def _protect_stored_flags() -> None:
    """Encrypt plaintext recoverable flags and re-encrypt old ones with the
    newest key (key rotation). Idempotent."""
    from sqlalchemy import select
    from security import init_flag_crypto, rotate_flag
    if not init_flag_crypto(settings.FLAG_KEYS_FILE):
        log.warning("scenario.flags.plaintext", msg="FLAG_KEYS_FILE not set — flags stored in clear")
        return
    async with db.get_sessionmaker()() as s:
        rows = (await s.execute(select(db.Challenge).where(db.Challenge.flag != ""))).scalars().all()
        changed = 0
        for c in rows:
            new = rotate_flag(c.flag)
            if new != c.flag:
                c.flag, changed = new, changed + 1
        await s.commit()
    log.info("scenario.flags.protected", rows=len(rows), reencrypted=changed)


async def _on_startup(_app: FastAPI) -> None:
    if settings.JWT_SECRET == "change-me-in-prod":
        log.warning("scenario.jwt.default_secret_in_use")

    Path(settings.CHALLENGE_FILES_DIR).mkdir(parents=True, exist_ok=True)
    db.init_engine(settings.DATABASE_URL)
    await db.create_schema()
    await _protect_stored_flags()
    log.info("scenario.db.ready",
             url=settings.DATABASE_URL.split("@")[-1],
             files_dir=settings.CHALLENGE_FILES_DIR)

    # Best-effort: make sure the player-container network exists. If the
    # Docker socket isn't reachable we keep going so non-docker challenges
    # still work; the readiness probe will report the failure.
    if await docker_runtime.docker_socket_reachable():
        try:
            await docker_runtime.ensure_player_network(settings.DOCKER_PLAYER_NETWORK,
                                                       subnet=settings.DOCKER_PLAYER_SUBNET)
            log.info("scenario.docker.network.ready",
                     network=settings.DOCKER_PLAYER_NETWORK)
        except Exception as exc:
            log.warning("scenario.docker.network.failed", error=str(exc))
    else:
        log.warning("scenario.docker.socket_unreachable",
                    msg="Docker daemon not reachable — docker challenges disabled")

    # NOTE: session auto-expiration is intentionally disabled — admins want
    # player containers to survive page refreshes. Sessions live until the
    # player (or an admin) explicitly stops them.
    _app.state.cleanup_task = None


async def _on_shutdown(_app: FastAPI) -> None:
    task = getattr(_app.state, "cleanup_task", None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await db.dispose_engine()


async def _cleanup_loop() -> None:
    """Reap sessions whose `expires_at` is in the past."""
    interval = max(15, int(settings.DOCKER_CLEANUP_INTERVAL_SECONDS))
    while True:
        try:
            await _reap_expired_sessions()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("scenario.cleanup.failed", error=str(exc))
        await asyncio.sleep(interval)


async def _reap_expired_sessions() -> None:
    Session = db.get_sessionmaker()
    now = datetime.now(timezone.utc)

    async with Session() as s:
        rows = (await s.execute(
            select(DockerSession).where(
                DockerSession.status     == "running",
                DockerSession.expires_at <  now,
            )
        )).scalars().all()

        if not rows:
            return

        for sess in rows:
            sess.status     = "stopped"
            sess.stopped_at = now
        await s.commit()
        expired = [(r.id, r.container_id, r.challenge_id, r.user_id) for r in rows]

    for sid, cid, chal_id, user_id in expired:
        if cid:
            await docker_runtime.stop_session_container(cid)
        log.info("scenario.session.expired",
                 session_id=sid, challenge_id=chal_id, user_id=user_id)


# Readiness probes

async def _consul_ready() -> bool:
    return isinstance(await app.state.registry.list_services(), dict)


async def _rabbit_ready() -> bool:
    publisher = app.state.publisher
    return await publisher.ensure_ready()


# Build app

app = create_app(
    settings,
    routers=[router],
    on_startup=[_on_startup],
    on_shutdown=[_on_shutdown],
    readiness_checks={
        "consul":   _consul_ready,
        "rabbitmq": _rabbit_ready,
        "postgres": db.ping,
        "docker":   docker_runtime.docker_socket_reachable,
    },
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
