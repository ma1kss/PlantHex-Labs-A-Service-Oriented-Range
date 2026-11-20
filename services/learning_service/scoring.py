from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import Participant, Solve, get_sessionmaker


def points_for(difficulty: str, policy: dict[str, int]) -> int:
    return policy.get((difficulty or "").strip().lower(), policy.get("default", 0))


async def award_solve(*, user_id: str, username: str, challenge_id: str,
                      difficulty: str, policy: dict[str, int]) -> int | None:
    """Record a first solve and award its points. Returns the points awarded,
    or None if this (user, challenge) was already solved (no double points)."""
    points = points_for(difficulty, policy)

    async with get_sessionmaker()() as s:
        # 1. Ensure the participant row exists (race-safe).
        await s.execute(
            pg_insert(Participant)
            .values(user_id=user_id, username=username or "",
                    team="", total_score=0, solve_count=0)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )

        # 2. Record the solve once (race-safe dedupe on the unique constraint).
        res = await s.execute(
            pg_insert(Solve)
            .values(id=str(uuid.uuid4()), user_id=user_id, challenge_id=challenge_id,
                    difficulty=(difficulty or "").strip().lower(), points=points,
                    solved_at=datetime.now(timezone.utc))
            .on_conflict_do_nothing(constraint="uq_solve_user_challenge")
            .returning(Solve.id)
        )
        if res.first() is None:
            await s.commit()      # already solved — nothing to award
            return None

        # 3. A fresh solve was recorded — bump the participant's totals.
        await s.execute(
            update(Participant)
            .where(Participant.user_id == user_id)
            .values(total_score=Participant.total_score + points,
                    solve_count=Participant.solve_count + 1,
                    username=username or Participant.username,
                    updated_at=datetime.now(timezone.utc))
        )
        await s.commit()
        return points
