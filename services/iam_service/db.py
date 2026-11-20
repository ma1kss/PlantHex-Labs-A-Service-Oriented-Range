from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, JSON, Text, Boolean, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id:            Mapped[str]       = mapped_column(String(36), primary_key=True,
                                                     default=lambda: str(uuid.uuid4()))
    username:      Mapped[str]       = mapped_column(String(64), unique=True, index=True, nullable=False)
    email:         Mapped[str]       = mapped_column(String(255), default="", nullable=False)
    password_hash: Mapped[str]       = mapped_column(String(255), nullable=False)
    roles:         Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Profile (F16) — editable self-service fields
    display_name:  Mapped[str]       = mapped_column(String(80),  default="", nullable=False)
    bio:           Mapped[str]       = mapped_column(Text,        default="", nullable=False)
    avatar:        Mapped[str]       = mapped_column(String(64),  default="", nullable=False)  # preset key
    created_at:    Mapped[datetime]  = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class PlatformSetting(Base):
    """Admin-editable key/value platform configuration (e.g. the registration
    secret). Values are read at request time so a change applies immediately;
    already-registered users are unaffected."""
    __tablename__ = "platform_settings"

    key:        Mapped[str] = mapped_column(String(64), primary_key=True)
    value:      Mapped[str] = mapped_column(String(255), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AccessGrant(Base):
    """An access grant issued (F02) when a challenge instance is launched, and
    revoked on teardown. IAM owns authorization; this is the record Orchestration
    asks IAM to create/revoke as part of the launch workflow."""
    __tablename__ = "access_grants"

    id:         Mapped[str]  = mapped_column(String(36), primary_key=True,
                                             default=lambda: str(uuid.uuid4()))
    user_id:    Mapped[str]  = mapped_column(String(36), index=True, nullable=False)
    resource:   Mapped[str]  = mapped_column(String(128), nullable=False, default="")   # e.g. challenge id
    session_id: Mapped[str]  = mapped_column(String(36), nullable=False, default="")
    active:     Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRevocation(Base):
    """Every token of `user_id` issued before `not_before` is revoked (SEC-022).
    Kept in its own table so it survives the deletion of the user row."""
    __tablename__ = "session_revocations"
    user_id:    Mapped[str]      = mapped_column(String(36), primary_key=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason:     Mapped[str]      = mapped_column(String(128), nullable=False, default="")


class RevokedToken(Base):
    """A single revoked token (logout). Kept until the token would expire."""
    __tablename__ = "revoked_tokens"
    jti:        Mapped[str]      = mapped_column(String(36), primary_key=True)
    user_id:    Mapped[str]      = mapped_column(String(36), index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


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
        # Idempotent migrations for profile columns added after first create
        # (create_all never ALTERs an existing table).
        for ddl in (
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(80)  NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS bio          TEXT         NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar       VARCHAR(64)  NOT NULL DEFAULT ''",
            # MFA was prototyped and withdrawn; drop its columns if present.
            "ALTER TABLE users DROP COLUMN IF EXISTS mfa_secret",
            "ALTER TABLE users DROP COLUMN IF EXISTS mfa_enabled",
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
