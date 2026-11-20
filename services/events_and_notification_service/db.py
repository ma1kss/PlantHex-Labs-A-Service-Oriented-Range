from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Announcement(Base):
    __tablename__ = "announcements"

    id:           Mapped[str] = mapped_column(String(36), primary_key=True,
                                              default=lambda: str(uuid.uuid4()))
    title:        Mapped[str] = mapped_column(String(255), nullable=False)
    body:         Mapped[str] = mapped_column(String, nullable=False, default="")
    author_id:    Mapped[str] = mapped_column(String(36),  nullable=False, default="")
    author_name:  Mapped[str] = mapped_column(String(64),  nullable=False, default="")
    author_role:  Mapped[str] = mapped_column(String(16),  nullable=False, default="")
    created_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


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
