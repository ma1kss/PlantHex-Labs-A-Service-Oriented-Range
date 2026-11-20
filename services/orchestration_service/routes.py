from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from cyber_range_core import UserContext, get_logger

import clients
import schemas
from db import Launch, get_sessionmaker
from security import require_auth_local, require_role


STAFF_ROLES = ("admin", "instructor")

log    = get_logger(__name__)
router = APIRouter()


def _token(request: Request) -> str | None:
    return request.headers.get("authorization")


def _is_staff(user: UserContext) -> bool:
    return any(r in user.roles for r in STAFF_ROLES)


def _session_ref(sess: dict | None) -> schemas.SessionRef | None:
    if not sess:
        return None
    return schemas.SessionRef(
        id=sess.get("id", ""), challenge_id=sess.get("challenge_id", ""),
        user_id=sess.get("user_id", ""), username=sess.get("username", ""),
        status=sess.get("status", ""), hostname=sess.get("hostname", "localhost"),
        ports=dict(sess.get("ports") or {}), access_urls=list(sess.get("access_urls") or []),
        kind=sess.get("kind", "single") or "single", subnet=sess.get("subnet", "") or "",
    )


def _endpoint(sess: dict) -> str:
    host_port = next((v for v in (sess.get("ports") or {}).values() if v), "")
    return f"{sess.get('hostname', 'localhost')}:{host_port}" if host_port else ""


@router.post("/launches",
             response_model=schemas.LaunchResult,
             status_code=status.HTTP_201_CREATED,
             summary="Launch a challenge instance (coordinates deploy → grant → register → observe)")
async def launch(body: schemas.LaunchRequest,
                 request: Request,
                 caller: UserContext = Depends(require_auth_local)):
    app   = request.app
    token = _token(request)
    steps: list[str] = []
    warnings: list[str] = []

    # 1. Scenario Management — deploy the instance. Critical: abort on failure.
    try:
        sess = await clients.deploy_session(app, token, body.challenge_id)
    except clients.PeerError as exc:
        code = exc.status if exc.status in (400, 403, 404, 409, 503) else 502
        raise HTTPException(code, f"deploy failed: {exc.detail}")
    steps.append("deploy")
    session_id = (sess or {}).get("id", "")
    endpoint   = _endpoint(sess or {})

    # 2. IAM — grant access (best-effort).
    grant_id = ""
    try:
        g = await clients.grant_access(app, token, resource=body.challenge_id, session_id=session_id)
        grant_id = (g or {}).get("grant_id", "")
        steps.append("grant")
    except clients.PeerError as exc:
        warnings.append(f"iam grant: {exc.detail}")

    # 3. Learning & Progress — register the attempt (best-effort).
    try:
        await clients.register_attempt(app, token, challenge_id=body.challenge_id)
        steps.append("register")
    except clients.PeerError as exc:
        warnings.append(f"learning register: {exc.detail}")

    # 4. Monitoring — begin observation (best-effort).
    observation_id = ""
    try:
        o = await clients.begin_observation(app, token, session_id=session_id,
                                            challenge_id=body.challenge_id, target=endpoint)
        observation_id = (o or {}).get("observation_id", "")
        steps.append("observe")
    except clients.PeerError as exc:
        warnings.append(f"monitoring observe: {exc.detail}")

    # Record the workflow so teardown can reverse it precisely.
    async with get_sessionmaker()() as s:
        rec = Launch(
            challenge_id=body.challenge_id, user_id=caller.id,
            username=caller.username or "", session_id=session_id,
            grant_id=grant_id, observation_id=observation_id,
            status="active", warnings=warnings,
        )
        s.add(rec)
        await s.commit()
        await s.refresh(rec)
        launch_id = rec.id

    await app.state.publisher.publish(
        "orchestration.launch.started",
        {"launch_id": launch_id, "challenge_id": body.challenge_id,
         "user_id": caller.id, "session_id": session_id,
         "steps": steps, "warnings": warnings},
        correlation_id=request.state.correlation_id,
    )
    log.info("orchestration.launch.started", launch_id=launch_id,
             challenge_id=body.challenge_id, user_id=caller.id, steps=steps)

    return schemas.LaunchResult(
        launch_id=launch_id, challenge_id=body.challenge_id,
        session=_session_ref(sess), steps=steps, warnings=warnings,
    )


@router.delete("/sessions/{session_id}",
               response_model=schemas.TeardownResult,
               summary="Tear down a launched instance (reverses observe → register → grant → deploy)")
async def teardown(session_id: str,
                   request: Request,
                   caller: UserContext = Depends(require_auth_local)):
    app   = request.app
    token = _token(request)
    steps: list[str] = []
    warnings: list[str] = []

    async with get_sessionmaker()() as s:
        rec = (await s.execute(
            select(Launch).where(Launch.session_id == session_id, Launch.status == "active")
        )).scalars().first()
    launch_id = rec.id if rec else None
    # When staff tear down someone else's instance, act on the owner's behalf.
    on_behalf = rec.user_id if (rec and rec.user_id != caller.id) else None

    # Reverse order — 1. Monitoring stop observation (best-effort).
    if rec and rec.observation_id:
        try:
            await clients.stop_observation(app, token, rec.observation_id)
            steps.append("observe-stop")
        except clients.PeerError as exc:
            warnings.append(f"monitoring stop: {exc.detail}")

    # 2. Learning end attempt (best-effort).
    if rec:
        try:
            await clients.end_attempt(app, token, challenge_id=rec.challenge_id, user_id=on_behalf)
            steps.append("attempt-end")
        except clients.PeerError as exc:
            warnings.append(f"learning end: {exc.detail}")

    # 3. IAM revoke access (best-effort).
    if rec and rec.grant_id:
        try:
            await clients.revoke_access(app, token, rec.grant_id)
            steps.append("revoke")
        except clients.PeerError as exc:
            warnings.append(f"iam revoke: {exc.detail}")

    # 4. Scenario Management teardown. Critical: this is what stops the container.
    try:
        await clients.teardown_session(app, token, session_id)
        steps.append("teardown")
    except clients.PeerError as exc:
        code = exc.status if exc.status in (403, 404, 503) else 502
        raise HTTPException(code, f"teardown failed: {exc.detail}")

    if rec:
        async with get_sessionmaker()() as s:
            r2 = await s.get(Launch, rec.id)
            if r2:
                r2.status = "torn_down"
                r2.torn_down_at = datetime.now(timezone.utc)
                r2.warnings = list(r2.warnings or []) + warnings
                await s.commit()

    await app.state.publisher.publish(
        "orchestration.launch.ended",
        {"launch_id": launch_id, "session_id": session_id,
         "user_id": caller.id, "steps": steps, "warnings": warnings},
        correlation_id=request.state.correlation_id,
    )
    log.info("orchestration.launch.ended", launch_id=launch_id,
             session_id=session_id, steps=steps)

    return schemas.TeardownResult(
        launch_id=launch_id, session_id=session_id,
        status="torn_down", steps=steps, warnings=warnings,
    )


@router.get("/launches",
            response_model=list[schemas.LaunchOut],
            summary="List launch records (admin / instructor)")
async def list_launches(_: UserContext = Depends(require_role(*STAFF_ROLES))):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(Launch).order_by(Launch.created_at.desc())
        )).scalars().all()
    return [
        schemas.LaunchOut(
            id=r.id, challenge_id=r.challenge_id, user_id=r.user_id,
            username=r.username, session_id=r.session_id, status=r.status,
            warnings=list(r.warnings or []), created_at=r.created_at,
            torn_down_at=r.torn_down_at)
        for r in rows
    ]
