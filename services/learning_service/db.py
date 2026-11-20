from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Participant(Base):
    __tablename__ = "participants"

    user_id:     Mapped[str] = mapped_column(String(36), primary_key=True)
    username:    Mapped[str] = mapped_column(String(64), nullable=False, default="")
    team:        Mapped[str] = mapped_column(String(64), nullable=False, default="")
    total_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    solve_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Team(Base):
    """A named team (F07). Participants reference it by name in their `team`
    column, so existing scoring/scoreboard code keeps working unchanged."""
    __tablename__ = "teams"

    id:         Mapped[str] = mapped_column(String(36), primary_key=True,
                                            default=lambda: str(uuid.uuid4()))
    name:       Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    motto:      Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Solve(Base):
    __tablename__ = "solves"
    __table_args__ = (
        UniqueConstraint("user_id", "challenge_id", name="uq_solve_user_challenge"),
    )

    id:           Mapped[str] = mapped_column(String(36), primary_key=True,
                                              default=lambda: str(uuid.uuid4()))
    user_id:      Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    challenge_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    difficulty:   Mapped[str] = mapped_column(String(16), nullable=False, default="")
    points:       Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    solved_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ExerciseEnrolment(Base):
    """A participant enrolled in one exercise (a scheduled CTF event), with
    the team and the exercise role they play in it (F07)."""
    __tablename__ = "exercise_enrolments"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_enrolment_event_user"),
    )
    id:          Mapped[str] = mapped_column(String(36), primary_key=True,
                                             default=lambda: str(uuid.uuid4()))
    event_id:    Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id:     Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    username:    Mapped[str] = mapped_column(String(64), nullable=False, default="")
    team:        Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role:        Mapped[str] = mapped_column(String(16), nullable=False, default="blue")
    enrolled_by: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    enrolled_at: Mapped[datetime] = mapped_column(
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
