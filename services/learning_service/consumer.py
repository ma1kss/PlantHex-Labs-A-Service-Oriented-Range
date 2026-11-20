from __future__ import annotations

from sqlalchemy import delete

from cyber_range_core import EventConsumer, get_logger

from db import ExerciseEnrolment, Participant, Solve, get_sessionmaker
from scoring import award_solve


log = get_logger(__name__)


def build_consumer(*, url: str, exchange: str, queue: str,
                   policy: dict[str, int], publisher_ref) -> EventConsumer:
    consumer = EventConsumer(
        url=url,
        exchange_name=exchange,
        queue_name=queue,
        service_name="learning-service",
    )

    @consumer.on("scenario.flag.submitted")
    async def _on_submission(routing_key: str, payload: dict) -> None:
        if not payload.get("correct"):
            return
        user_id      = str(payload.get("user_id") or "")
        challenge_id = str(payload.get("challenge_id") or "")
        if not user_id or not challenge_id:
            return
        username   = str(payload.get("username") or "")
        difficulty = str(payload.get("difficulty") or "")

        awarded = await award_solve(
            user_id=user_id, username=username,
            challenge_id=challenge_id, difficulty=difficulty, policy=policy,
        )
        if awarded is None:
            return  # already solved — no double points

        log.info("learning.solve.scored",
                 user_id=user_id, challenge_id=challenge_id,
                 difficulty=difficulty, points=awarded)

        # Announce the score change (best-effort).
        pub = publisher_ref()
        if pub is not None:
            try:
                await pub.publish(
                    "learning.solve.scored",
                    {"user_id": user_id, "username": username,
                     "challenge_id": challenge_id, "difficulty": difficulty,
                     "points": awarded},
                )
            except Exception as exc:
                log.warning("learning.publish_failed", error=str(exc))

    @consumer.on("iam.user.deleted")
    async def _on_user_deleted(routing_key: str, payload: dict) -> None:
        user_id = str(payload.get("user_id") or "")
        if not user_id:
            return
        async with get_sessionmaker()() as s:
            await s.execute(delete(Solve).where(Solve.user_id == user_id))
            await s.execute(delete(ExerciseEnrolment).where(ExerciseEnrolment.user_id == user_id))
            res = await s.execute(
                delete(Participant).where(Participant.user_id == user_id))
            await s.commit()
        if res.rowcount:
            log.info("learning.participant.removed", user_id=user_id,
                     reason="iam.user.deleted")

    @consumer.on("scenario.challenge.deleted")
    async def _on_challenge_deleted(routing_key: str, payload: dict) -> None:
        challenge_id = str(payload.get("challenge_id") or "")
        if challenge_id:
            n = await remove_challenge_solves([challenge_id])
            if n:
                log.info("learning.solves.removed", challenge_id=challenge_id, solves=n)

    @consumer.on("scenario.event.deleted")
    async def _on_event_deleted(routing_key: str, payload: dict) -> None:
        event_id = str(payload.get("event_id") or "")
        if not event_id:
            return
        async with get_sessionmaker()() as s:
            res = await s.execute(delete(ExerciseEnrolment).where(ExerciseEnrolment.event_id == event_id))
            await s.commit()
        if res.rowcount:
            log.info("learning.enrolments.removed", event_id=event_id, count=res.rowcount)

    return consumer


async def remove_challenge_solves(challenge_ids: list[str]) -> int:
    """Delete every solve of the given challenges and subtract their points
    from the owners' totals, in one transaction."""
    if not challenge_ids:
        return 0
    from sqlalchemy import select
    async with get_sessionmaker()() as s:
        solves = (await s.execute(
            select(Solve).where(Solve.challenge_id.in_(challenge_ids)))).scalars().all()
        for sv in solves:
            p = await s.get(Participant, sv.user_id)
            if p is not None:
                p.total_score = max(0, p.total_score - sv.points)
                p.solve_count = max(0, p.solve_count - 1)
            await s.delete(sv)
        await s.commit()
    return len(solves)
