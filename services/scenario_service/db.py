from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Text, String, DateTime, Integer, Boolean, Float, JSON, ForeignKey, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Challenge(Base):
    __tablename__ = "challenges"

    id:            Mapped[str]       = mapped_column(String(36), primary_key=True,
                                                     default=lambda: str(uuid.uuid4()))
    title:         Mapped[str]       = mapped_column(String(255), nullable=False)
    description:   Mapped[str]       = mapped_column(String, nullable=False, default="")
    category:      Mapped[str]       = mapped_column(String(64), nullable=False, default="misc")
    # "static" (downloadable file), "docker" (per-player container) or
    # "lab" (multi-container compose topology reached over WireGuard).
    challenge_type: Mapped[str]      = mapped_column(String(16), nullable=False, default="static")
    # Whether players can see this challenge. Staff always see everything.
    visible:        Mapped[bool]     = mapped_column(Boolean, nullable=False, default=True)
    difficulty:    Mapped[str]       = mapped_column(String(16), nullable=False, default="easy")
    points:        Mapped[int]       = mapped_column(Integer, nullable=False, default=100)
    flag_hash:     Mapped[str]       = mapped_column(String(255), nullable=False)
    # Plaintext flag, kept so admins can view/recover it in the edit form.
    # `flag_hash` remains the source of truth for constant-time matching;
    # this is only ever returned through the admin-only flag endpoint.
    flag:          Mapped[str]       = mapped_column(Text, nullable=False, default="")
    file_path:     Mapped[str]       = mapped_column(String(1024), nullable=False, default="")
    file_name:     Mapped[str]       = mapped_column(String(255), nullable=False, default="")
    author:        Mapped[str]       = mapped_column(String(64), nullable=False, default="")
    max_attempts:  Mapped[int]       = mapped_column(Integer, nullable=False, default=0)  # 0 = unlimited
    tags:          Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at:    Mapped[datetime]  = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Submission(Base):
    __tablename__ = "submissions"

    id:           Mapped[str]      = mapped_column(String(36), primary_key=True,
                                                   default=lambda: str(uuid.uuid4()))
    challenge_id: Mapped[str]      = mapped_column(String(36), nullable=False, index=True)
    user_id:      Mapped[str]      = mapped_column(String(36), nullable=False, index=True)
    username:     Mapped[str]      = mapped_column(String(64), nullable=False, default="")
    correct:      Mapped[bool]     = mapped_column(Boolean, nullable=False, default=False)
    points:       Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CtfEvent(Base):
    """A time-boxed competition event grouping selected challenges.

    Challenges assigned to an event leave the always-on playground and are
    playable only while one of their events is live (staff bypass the gate).
    Named Ctf* to avoid any confusion with the platform event bus.
    """
    __tablename__ = "ctf_events"

    id:          Mapped[str]      = mapped_column(String(36), primary_key=True,
                                                  default=lambda: str(uuid.uuid4()))
    name:        Mapped[str]      = mapped_column(String(255), nullable=False)
    description: Mapped[str]      = mapped_column(String, nullable=False, default="")
    starts_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    author:      Mapped[str]      = mapped_column(String(64), nullable=False, default="")
    created_by:  Mapped[str]      = mapped_column(String(36), nullable=False, default="")
    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CtfEventChallenge(Base):
    """Membership: which challenges belong to which event."""
    __tablename__ = "ctf_event_challenges"

    event_id:     Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ctf_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    challenge_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("challenges.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_at:     Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class DockerSpec(Base):
    """Docker-specific build metadata for a challenge.

    One-to-one with `challenges` — only present when the challenge's category
    is "docker". Kept in a separate table so the existing Challenge schema
    is not touched.
    """
    __tablename__ = "docker_specs"

    challenge_id:     Mapped[str]       = mapped_column(
        String(36),
        ForeignKey("challenges.id", ondelete="CASCADE"),
        primary_key=True,
    )
    image_tag:        Mapped[str]       = mapped_column(String(255), nullable=False, default="")
    base_image:       Mapped[str]       = mapped_column(String(255), nullable=False, default="")
    dockerfile_hash:  Mapped[str]       = mapped_column(String(64),  nullable=False, default="")
    exposed_ports:    Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    cpu_limit:                Mapped[float] = mapped_column(Float,       nullable=False, default=1.0)
    memory_limit:             Mapped[str]   = mapped_column(String(16),  nullable=False, default="512m")
    pids_limit:               Mapped[int]   = mapped_column(Integer,     nullable=False, default=200)
    session_ttl_minutes:      Mapped[int]   = mapped_column(Integer,     nullable=False, default=120)
    max_instances_per_player: Mapped[int]   = mapped_column(Integer,     nullable=False, default=1)

    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    rebuilt_at:  Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LabSpec(Base):
    """Multi-container lab definition for a challenge_type='lab' challenge.

    One-to-one with `challenges`. Stores the admin-uploaded Docker Compose file
    verbatim; the lab runtime parses a safe subset of it at launch time.
    """
    __tablename__ = "lab_specs"

    challenge_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("challenges.id", ondelete="CASCADE"),
        primary_key=True,
    )
    compose_yaml: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    rebuilt_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DockerSession(Base):
    """One running player instance.

    For a 'docker' challenge this is a single container. For a 'lab' challenge
    it fronts a whole topology: an isolated network/subnet, the compose
    containers, and an injected WireGuard server the player tunnels into. The
    lab fields below are empty for single-container sessions.
    """
    __tablename__ = "docker_sessions"

    id:             Mapped[str] = mapped_column(String(36), primary_key=True,
                                                default=lambda: str(uuid.uuid4()))
    challenge_id:   Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id:        Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    username:       Mapped[str] = mapped_column(String(64), nullable=False, default="")

    container_id:   Mapped[str] = mapped_column(String(128), nullable=False, default="")
    container_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # JSON dict like {"22/tcp": "32145"} — container port → host port
    ports:          Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    hostname:       Mapped[str]  = mapped_column(String(255), nullable=False, default="localhost")

    # Lab fields (empty for single-container 'docker' sessions)
    kind:              Mapped[str]  = mapped_column(String(16), nullable=False, default="single")  # single | lab
    network_name:      Mapped[str]  = mapped_column(String(128), nullable=False, default="")
    subnet:            Mapped[str]  = mapped_column(String(32), nullable=False, default="")        # 10.10.X.0/24
    subnet_index:      Mapped[int]  = mapped_column(Integer, nullable=False, default=0)            # the X (0 = none)
    lab_container_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)            # all containers incl WG
    wg_port:           Mapped[str]  = mapped_column(String(8), nullable=False, default="")         # host UDP port
    wg_server_pubkey:  Mapped[str]  = mapped_column(String(64), nullable=False, default="")
    client_conf:       Mapped[str]  = mapped_column(String, nullable=False, default="")            # player .conf
    # Per-service lab endpoints: [{"name","ip","ports":[...]}] — shown to the
    # player so each container has its own reachable address over the VPN.
    lab_endpoints:     Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # creating | running | stopped | error
    status:         Mapped[str]  = mapped_column(String(16), nullable=False, default="creating", index=True)
    error_message:  Mapped[str]  = mapped_column(String(1024), nullable=False, default="")

    created_at:     Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stopped_at:     Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


_engine = None
_Session: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> None:
    global _engine, _Session
    _engine  = create_async_engine(database_url, pool_pre_ping=True)
    _Session = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _Session is None:
        raise RuntimeError("DB not initialised — call init_engine() first")
    return _Session


async def create_schema() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight, idempotent migrations for columns added after a table
        # was first created (create_all never ALTERs existing tables).
        for ddl in (
            "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS flag VARCHAR(255) NOT NULL DEFAULT ''",
            # Encrypted flags are longer than the plaintext (SEC-005).
            "ALTER TABLE challenges ALTER COLUMN flag TYPE TEXT",
            "ALTER TABLE challenges ADD COLUMN IF NOT EXISTS visible BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'single'",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS network_name VARCHAR(128) NOT NULL DEFAULT ''",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS subnet VARCHAR(32) NOT NULL DEFAULT ''",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS subnet_index INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS lab_container_ids JSON NOT NULL DEFAULT '[]'",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS wg_port VARCHAR(8) NOT NULL DEFAULT ''",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS wg_server_pubkey VARCHAR(64) NOT NULL DEFAULT ''",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS client_conf TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE docker_sessions ADD COLUMN IF NOT EXISTS lab_endpoints JSON NOT NULL DEFAULT '[]'",
        ):
            await conn.execute(text(ddl))


async def dispose_engine() -> None:
    if _engine is not None:
        await _engine.dispose()


async def ping() -> bool:
    if _engine is None:
        return False
    try:
        async with _engine.connect() as conn:
            await conn.execute(select(1))
        return True
    except Exception:
        return False
