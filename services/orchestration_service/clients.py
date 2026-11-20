from __future__ import annotations

import httpx
from fastapi import FastAPI

from cyber_range_core import get_logger


log = get_logger(__name__)


class PeerError(Exception):
    def __init__(self, service: str, status: int, detail: str):
        self.service = service
        self.status = status
        self.detail = detail
        super().__init__(f"{service} -> {status}: {detail}")


def _auth(token: str | None) -> dict[str, str]:
    return {"Authorization": token} if token else {}


async def _base(app: FastAPI, service: str) -> str | None:
    try:
        endpoint = await app.state.registry.get_one(service)
        if endpoint is not None:
            return endpoint.base_url
    except Exception as exc:
        log.warning("orch.resolve.failed", service=service, error=str(exc))
    return app.state.settings.downstream_map().get(service)


async def _request(app, service, method, path, *, token=None, json=None, ok=(200, 201, 204)):
    base = await _base(app, service)
    if base is None:
        raise PeerError(service, 503, f"'{service}' is not registered")
    url = base.rstrip("/") + path
    # Generous total timeout: a lab deploy (build WireGuard image, pull service
    # images, start the topology) can take a while on a cold platform.
    async with httpx.AsyncClient(timeout=httpx.Timeout(150.0, connect=5.0)) as cli:
        try:
            r = await cli.request(method, url, headers=_auth(token), json=json)
        except httpx.RequestError as exc:
            raise PeerError(service, 502, str(exc))
    if r.status_code not in ok:
        detail = ""
        try:
            detail = (r.json() or {}).get("detail", "")
        except Exception:
            detail = (r.text or "")[:200]
        raise PeerError(service, r.status_code, detail or f"HTTP {r.status_code}")
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except Exception:
        return None


# Scenario Management — instance lifecycle (deploy / teardown)

async def deploy_session(app, token, challenge_id):
    return await _request(app, "scenario-service", "POST",
                          f"/challenges/{challenge_id}/session", token=token, ok=(201,))


async def teardown_session(app, token, session_id):
    # 404 tolerated — the instance may already be gone.
    return await _request(app, "scenario-service", "DELETE",
                          f"/sessions/{session_id}", token=token, ok=(200, 204, 404))


# IAM — access grant / revoke (F02)

async def grant_access(app, token, *, resource, session_id, user_id=None):
    return await _request(app, "iam-service", "POST", "/access/grant", token=token,
                          json={"resource": resource, "session_id": session_id, "user_id": user_id},
                          ok=(201,))


async def revoke_access(app, token, grant_id):
    return await _request(app, "iam-service", "POST", "/access/revoke", token=token,
                          json={"grant_id": grant_id}, ok=(200, 404))


# Learning & Progress — register / end attempt

async def register_attempt(app, token, *, challenge_id, user_id=None):
    return await _request(app, "learning-service", "POST", "/attempts", token=token,
                          json={"challenge_id": challenge_id, "user_id": user_id}, ok=(201,))


async def end_attempt(app, token, *, challenge_id, user_id=None):
    return await _request(app, "learning-service", "POST", "/attempts/end", token=token,
                          json={"challenge_id": challenge_id, "user_id": user_id}, ok=(200,))


# Monitoring — begin / stop observation

async def begin_observation(app, token, *, session_id, challenge_id, target="", user_id=None):
    return await _request(app, "monitoring-service", "POST", "/observations", token=token,
                          json={"session_id": session_id, "challenge_id": challenge_id,
                                "target": target, "user_id": user_id}, ok=(201,))


async def stop_observation(app, token, observation_id):
    return await _request(app, "monitoring-service", "DELETE",
                          f"/observations/{observation_id}", token=token, ok=(200, 204, 404))
