from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from cyber_range_core import UserContext, get_logger

import schemas
from db import ExerciseEnrolment, Participant, Solve, Team, get_sessionmaker
from security import require_auth_local, require_role


STAFF_ROLES = ("admin", "instructor")

log    = get_logger(__name__)
router = APIRouter()


def _policy(request: Request) -> dict[str, int]:
    return request.app.state.points_policy


async def _rank_of(session, total_score: int) -> int:
    """1-based rank: how many participants score strictly higher, +1."""
    higher = (await session.execute(
        select(func.count()).select_from(Participant)
        .where(Participant.total_score > total_score)
    )).scalar_one()
    return higher + 1


# Learner self-view (F10)

@router.get("/me",
            response_model=schemas.MeOut,
            summary="My score, rank and solves")
async def me(user: UserContext = Depends(require_auth_local)) -> schemas.MeOut:
    async with get_sessionmaker()() as s:
        p = await s.get(Participant, user.id)
        if p is None:
            total = (await s.execute(
                select(func.count()).select_from(Participant))).scalar_one()
            return schemas.MeOut(
                user_id=user.id, username=user.username, team="",
                total_score=0, solve_count=0, rank=total + 1, solves=[],
            )
        rank = await _rank_of(s, p.total_score)
        solves = (await s.execute(
            select(Solve).where(Solve.user_id == user.id)
            .order_by(Solve.solved_at.desc())
        )).scalars().all()

    return schemas.MeOut(
        user_id=p.user_id, username=p.username, team=p.team,
        total_score=p.total_score, solve_count=p.solve_count, rank=rank,
        solves=[schemas.SolveOut(
            challenge_id=sv.challenge_id, difficulty=sv.difficulty,
            points=sv.points, solved_at=sv.solved_at) for sv in solves],
    )


# Scoreboard (F10)

@router.get("/leaderboard",
            response_model=list[schemas.LeaderboardRow],
            summary="The scoreboard (any signed-in user)")
async def leaderboard(
    _: UserContext = Depends(require_auth_local),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(Participant)
            .order_by(Participant.total_score.desc(),
                      Participant.solve_count.desc(),
                      Participant.username.asc())
            .limit(limit)
        )).scalars().all()

    return [
        schemas.LeaderboardRow(
            rank=i + 1, user_id=p.user_id, username=p.username, team=p.team,
            total_score=p.total_score, solve_count=p.solve_count)
        for i, p in enumerate(rows)
    ]


# Enrolment / teams (F07, staff)

@router.post("/participants",
             response_model=schemas.ParticipantOut,
             status_code=status.HTTP_201_CREATED,
             summary="Enrol a participant (admin / instructor)")
async def enrol(body: schemas.EnrolRequest,
                request: Request,
                staff: UserContext = Depends(require_role(*STAFF_ROLES))):
    """Idempotent: create the participant row if absent, else update the
    username/team. Lets staff pre-enrol participants and place them on teams
    before any solve. Scores are untouched."""
    async with get_sessionmaker()() as s:
        stmt = pg_insert(Participant).values(
            user_id=body.user_id, username=body.username or "",
            team=(body.team or "").strip(), total_score=0, solve_count=0,
        )
        # On conflict, only refresh username/team — never reset the score.
        update_cols = {"updated_at": datetime.now(timezone.utc)}
        if body.username:
            update_cols["username"] = body.username
        if body.team:
            update_cols["team"] = body.team.strip()
        stmt = stmt.on_conflict_do_update(index_elements=["user_id"], set_=update_cols)
        await s.execute(stmt)
        await s.commit()
        p = await s.get(Participant, body.user_id)
        out = schemas.ParticipantOut(
            user_id=p.user_id, username=p.username, team=p.team,
            total_score=p.total_score, solve_count=p.solve_count)

    log.info("learning.participant.enrolled",
             user_id=body.user_id, team=out.team, by=staff.id)
    return out


# Attempts (registered by the Orchestration launch workflow)

def _attempt_target(body: schemas.AttemptRequest, caller: UserContext) -> str:
    target = body.user_id or caller.id
    if body.user_id and body.user_id != caller.id and not any(r in caller.roles for r in STAFF_ROLES):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Registering an attempt for another user requires staff role")
    return target


@router.post("/attempts",
             status_code=status.HTTP_201_CREATED,
             summary="Register a challenge attempt (self or staff-on-behalf)")
async def register_attempt(body: schemas.AttemptRequest,
                           request: Request,
                           caller: UserContext = Depends(require_auth_local)):
    """Ensure the learner has a participant row (so they're on the roster) and
    record that an attempt started. Never awards or resets points."""
    target = _attempt_target(body, caller)
    username = caller.username if target == caller.id else ""
    async with get_sessionmaker()() as s:
        stmt = pg_insert(Participant).values(
            user_id=target, username=username or "", team="",
            total_score=0, solve_count=0,
        ).on_conflict_do_nothing(index_elements=["user_id"])
        await s.execute(stmt)
        await s.commit()

    await request.app.state.publisher.publish(
        "learning.attempt.registered",
        {"user_id": target, "challenge_id": body.challenge_id},
        correlation_id=request.state.correlation_id,
    )
    log.info("learning.attempt.registered", user_id=target, challenge_id=body.challenge_id)
    return {"ok": True, "user_id": target, "challenge_id": body.challenge_id}


@router.post("/attempts/end",
             summary="Mark a challenge attempt ended (self or staff-on-behalf)")
async def end_attempt(body: schemas.AttemptRequest,
                      request: Request,
                      caller: UserContext = Depends(require_auth_local)):
    target = _attempt_target(body, caller)
    await request.app.state.publisher.publish(
        "learning.attempt.ended",
        {"user_id": target, "challenge_id": body.challenge_id},
        correlation_id=request.state.correlation_id,
    )
    log.info("learning.attempt.ended", user_id=target, challenge_id=body.challenge_id)
    return {"ok": True, "user_id": target, "challenge_id": body.challenge_id}


@router.get("/participants",
            response_model=list[schemas.ParticipantOut],
            summary="Every participant's standing (admin / instructor)")
async def participants(_: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(Participant).order_by(Participant.total_score.desc())
        )).scalars().all()
    return [
        schemas.ParticipantOut(
            user_id=p.user_id, username=p.username, team=p.team,
            total_score=p.total_score, solve_count=p.solve_count)
        for p in rows
    ]


@router.delete("/participants/{user_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Remove a participant and their solves (admin / instructor). "
                       "Used to purge accounts that no longer exist in IAM.")
async def delete_participant(user_id: str,
                             staff: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        await s.execute(delete(Solve).where(Solve.user_id == user_id))
        res = await s.execute(delete(Participant).where(Participant.user_id == user_id))
        await s.commit()
    if not res.rowcount:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Participant not found")
    log.info("learning.participant.deleted", user_id=user_id, by=staff.id)
    return None


@router.put("/participants/{user_id}/team",
            response_model=schemas.ParticipantOut,
            summary="Assign a participant to a team (admin / instructor)")
async def assign_team(user_id: str,
                      body: schemas.AssignTeamRequest,
                      request: Request,
                      staff: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        p = await s.get(Participant, user_id)
        if p is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "Participant not found (they have no solves yet).")
        p.team = (body.team or "").strip()
        await s.commit()
        await s.refresh(p)
        out = schemas.ParticipantOut(
            user_id=p.user_id, username=p.username, team=p.team,
            total_score=p.total_score, solve_count=p.solve_count)

    await request.app.state.publisher.publish(
        "learning.team.assigned",
        {"user_id": user_id, "team": out.team, "by": staff.username or staff.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("learning.team.assigned", user_id=user_id, team=out.team, by=staff.id)
    return out


# Teams (F07)

def _member_out(p: Participant) -> schemas.TeamMember:
    return schemas.TeamMember(user_id=p.user_id, username=p.username,
                              total_score=p.total_score, solve_count=p.solve_count)


@router.get("/teams",
            response_model=list[schemas.TeamOut],
            summary="All teams with their rosters (any signed-in user)")
async def list_teams(_: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        teams = (await s.execute(select(Team).order_by(Team.name))).scalars().all()
        members = (await s.execute(
            select(Participant).where(Participant.team != "")
            .order_by(Participant.total_score.desc())
        )).scalars().all()
    by_team: dict[str, list[schemas.TeamMember]] = {}
    for p in members:
        by_team.setdefault(p.team, []).append(_member_out(p))
    return [schemas.TeamOut(id=t.id, name=t.name, motto=t.motto,
                            members=by_team.get(t.name, []))
            for t in teams]


@router.post("/teams",
             response_model=schemas.TeamOut,
             status_code=status.HTTP_201_CREATED,
             summary="Create a team (admin / instructor)")
async def create_team(body: schemas.TeamCreate,
                      request: Request,
                      staff: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        team = Team(name=body.name.strip(), motto=(body.motto or "").strip())
        s.add(team)
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "A team with that name already exists")
        await s.refresh(team)

    await request.app.state.publisher.publish(
        "learning.team.created",
        {"team_id": team.id, "name": team.name, "by": staff.username or staff.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("learning.team.created", team_id=team.id, name=team.name, by=staff.id)
    return schemas.TeamOut(id=team.id, name=team.name, motto=team.motto, members=[])


@router.put("/teams/{team_id}",
            response_model=schemas.TeamOut,
            summary="Update a team's motto (admin / instructor)")
async def update_team(team_id: str,
                      body: schemas.TeamUpdate,
                      _: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        team = await s.get(Team, team_id)
        if team is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
        team.motto = (body.motto or "").strip()
        await s.commit()
        await s.refresh(team)
    return schemas.TeamOut(id=team.id, name=team.name, motto=team.motto, members=[])


@router.delete("/teams/{team_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a team and unassign its members (admin / instructor)")
async def delete_team(team_id: str,
                      staff: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        team = await s.get(Team, team_id)
        if team is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
        await s.execute(update(Participant)
                        .where(Participant.team == team.name).values(team=""))
        await s.delete(team)
        await s.commit()
    log.info("learning.team.deleted", team_id=team_id, by=staff.id)
    return None


@router.get("/teams/{team_id}/solves",
            response_model=list[schemas.TeamSolveOut],
            summary="Every solve by a team's members (admin / instructor)")
async def team_solves(team_id: str,
                      _: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        team = await s.get(Team, team_id)
        if team is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
        rows = (await s.execute(
            select(Solve, Participant.username)
            .join(Participant, Participant.user_id == Solve.user_id)
            .where(Participant.team == team.name)
            .order_by(Solve.solved_at.desc())
        )).all()
    return [schemas.TeamSolveOut(
        challenge_id=sv.challenge_id, difficulty=sv.difficulty, points=sv.points,
        solved_at=sv.solved_at, user_id=sv.user_id, username=uname or "")
        for sv, uname in rows]


@router.get("/leaderboard/teams",
            response_model=list[schemas.TeamLeaderboardRow],
            summary="The team scoreboard — each challenge counts once per team")
async def team_leaderboard(_: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        teams = (await s.execute(select(Team))).scalars().all()
        # One row per (team, challenge): a challenge scores once per team no
        # matter how many members solved it.
        per_chal = (
            select(Participant.team.label("team"),
                   Solve.challenge_id.label("challenge_id"),
                   func.max(Solve.points).label("points"))
            .join(Participant, Participant.user_id == Solve.user_id)
            .where(Participant.team != "")
            .group_by(Participant.team, Solve.challenge_id)
        ).subquery()
        score_rows = (await s.execute(
            select(per_chal.c.team,
                   func.sum(per_chal.c.points),
                   func.count())
            .group_by(per_chal.c.team)
        )).all()
        count_rows = (await s.execute(
            select(Participant.team, func.count())
            .where(Participant.team != "")
            .group_by(Participant.team)
        )).all()

    scores  = {t: (int(total), int(n)) for t, total, n in score_rows}
    counts  = dict(count_rows)
    rows = sorted(
        ({"team_id": t.id, "team": t.name, "motto": t.motto,
          "member_count": counts.get(t.name, 0),
          "total_score": scores.get(t.name, (0, 0))[0],
          "solve_count": scores.get(t.name, (0, 0))[1]} for t in teams),
        key=lambda r: (-r["total_score"], -r["solve_count"], r["team"]),
    )
    return [schemas.TeamLeaderboardRow(rank=i + 1, **r) for i, r in enumerate(rows)]


# Solves & revocation (staff)

@router.get("/participants/{user_id}/solves",
            response_model=list[schemas.SolveOut],
            summary="A participant's solves (admin / instructor)")
async def participant_solves(user_id: str,
                             _: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        solves = (await s.execute(
            select(Solve).where(Solve.user_id == user_id)
            .order_by(Solve.solved_at.desc())
        )).scalars().all()
    return [schemas.SolveOut(challenge_id=sv.challenge_id, difficulty=sv.difficulty,
                             points=sv.points, solved_at=sv.solved_at)
            for sv in solves]


@router.post("/solves/revoke",
             summary="Revoke a solve for a player or a whole team (admin / instructor)")
async def revoke_solve(body: schemas.RevokeSolveRequest,
                       request: Request,
                       staff: UserContext = Depends(require_role(*STAFF_ROLES))):
    """Deletes the solve row(s) and takes the points back, so the challenge is
    unsolved again for the player / every member of the team."""
    if bool(body.user_id) == bool(body.team_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Provide exactly one of user_id or team_id")

    async with get_sessionmaker()() as s:
        if body.team_id:
            team = await s.get(Team, body.team_id)
            if team is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Team not found")
            user_ids = [uid for (uid,) in (await s.execute(
                select(Participant.user_id).where(Participant.team == team.name)
            )).all()]
        else:
            user_ids = [body.user_id]

        revoked: list[str] = []
        for uid in user_ids:
            solve = (await s.execute(select(Solve).where(
                Solve.user_id == uid,
                Solve.challenge_id == body.challenge_id,
            ))).scalar_one_or_none()
            if solve is None:
                continue
            points = solve.points
            await s.delete(solve)
            await s.execute(
                update(Participant)
                .where(Participant.user_id == uid)
                .values(total_score=Participant.total_score - points,
                        solve_count=Participant.solve_count - 1,
                        updated_at=datetime.now(timezone.utc)))
            revoked.append(uid)
        await s.commit()

    await request.app.state.publisher.publish(
        "learning.solve.revoked",
        {"challenge_id": body.challenge_id, "user_ids": revoked,
         "team_id": body.team_id or "", "by": staff.username or staff.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("learning.solve.revoked", challenge_id=body.challenge_id,
             revoked=len(revoked), by=staff.id)
    return {"challenge_id": body.challenge_id, "revoked_user_ids": revoked}


# Stats (staff)

@router.get("/stats",
            response_model=schemas.StatsOut,
            summary="Totals + the points policy (admin / instructor)")
async def stats(request: Request, _: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        participants_n = (await s.execute(
            select(func.count()).select_from(Participant))).scalar_one()
        total_solves   = (await s.execute(
            select(func.count()).select_from(Solve))).scalar_one()
        total_points   = (await s.execute(
            select(func.coalesce(func.sum(Solve.points), 0)))).scalar_one()
        by_diff_rows = (await s.execute(
            select(Solve.difficulty, func.count()).group_by(Solve.difficulty)
        )).all()

    policy = _policy(request)
    return schemas.StatsOut(
        participants=participants_n,
        total_solves=total_solves,
        total_points=int(total_points or 0),
        by_difficulty={d or "?": n for d, n in by_diff_rows},
        points_policy=schemas.PointsPolicy(
            easy=policy.get("easy", 0), medium=policy.get("medium", 0),
            hard=policy.get("hard", 0), default=policy.get("default", 0)),
    )


# Exercise enrolment (F07, SR-FR-BPL-23)
# A participant is enrolled per exercise (CTF event) with a team and an
# exercise role (red / blue / white / observer); platform roles stay in IAM.

def _enrol_out(e: ExerciseEnrolment) -> schemas.EnrolmentOut:
    return schemas.EnrolmentOut(event_id=e.event_id, user_id=e.user_id, username=e.username,
                                team=e.team, role=e.role, enrolled_at=e.enrolled_at)


@router.post("/exercises/{event_id}/enrolments",
             response_model=schemas.EnrolmentOut,
             status_code=status.HTTP_201_CREATED,
             summary="Enrol a participant in an exercise with a team and role (admin / instructor)")
async def enrol(event_id: str, body: schemas.EnrolmentIn, request: Request,
                staff: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        e = (await s.execute(select(ExerciseEnrolment).where(
            ExerciseEnrolment.event_id == event_id,
            ExerciseEnrolment.user_id == body.user_id))).scalar_one_or_none()
        if e is None:
            e = ExerciseEnrolment(event_id=event_id, user_id=body.user_id)
            s.add(e)
        e.username, e.team, e.role, e.enrolled_by = body.username, body.team, body.role, staff.id
        await s.commit()
        await s.refresh(e)
        out = _enrol_out(e)
    await request.app.state.publisher.publish(
        "learning.exercise.enrolled",
        {"event_id": event_id, "user_id": body.user_id, "username": body.username,
         "team": body.team, "role": body.role, "by": staff.id},
        correlation_id=request.state.correlation_id)
    return out


@router.get("/exercises/{event_id}/enrolments",
            response_model=list[schemas.EnrolmentOut],
            summary="Participants of an exercise with team and role (admin / instructor)")
async def enrolments(event_id: str, _: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(ExerciseEnrolment)
                                .where(ExerciseEnrolment.event_id == event_id)
                                .order_by(ExerciseEnrolment.team, ExerciseEnrolment.username)
                                )).scalars().all()
    return [_enrol_out(e) for e in rows]


@router.delete("/exercises/{event_id}/enrolments/{user_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Remove a participant from an exercise (admin / instructor)")
async def unenrol(event_id: str, user_id: str, request: Request,
                  staff: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        res = await s.execute(delete(ExerciseEnrolment).where(
            ExerciseEnrolment.event_id == event_id, ExerciseEnrolment.user_id == user_id))
        await s.commit()
    if not res.rowcount:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not enrolled")
    return None


@router.get("/me/exercises",
            response_model=list[schemas.EnrolmentOut],
            summary="The exercises you are enrolled in, with your team and role")
async def my_exercises(user: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(select(ExerciseEnrolment)
                                .where(ExerciseEnrolment.user_id == user.id))).scalars().all()
    return [_enrol_out(e) for e in rows]


# Consistency maintenance (SR-FR-IFL-43)

@router.post("/maintenance/reconcile",
             response_model=schemas.ReconcileOut,
             summary="Remove solves of challenges that no longer exist (admin)")
async def reconcile(body: schemas.ReconcileIn, request: Request,
                    admin: UserContext = Depends(require_role("admin"))):
    from consumer import remove_challenge_solves
    valid = set(body.valid_challenge_ids)
    async with get_sessionmaker()() as s:
        used = set((await s.execute(select(Solve.challenge_id).distinct())).scalars().all())
    orphans = sorted(used - valid)
    removed = await remove_challenge_solves(orphans)
    if removed:
        await request.app.state.publisher.publish(
            "learning.solves.reconciled",
            {"removed_solves": removed, "orphan_challenges": orphans, "by": admin.id},
            correlation_id=request.state.correlation_id)
    return schemas.ReconcileOut(removed_solves=removed, orphan_challenges=orphans)

