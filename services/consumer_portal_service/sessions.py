from __future__ import annotations

import asyncio
import time

import httpx

from cyber_range_core import EventConsumer, get_logger

log = get_logger(__name__)


class SessionGuard:
    def __init__(self, idle_timeout: int = 1800):
        self.idle_timeout = idle_timeout
        self.user_not_before: dict[str, int] = {}
        self.revoked_jti: dict[str, int] = {}
        self._last_seen: dict[str, float] = {}
        self.synced = False

    # decisions
    def check(self, claims: dict) -> str | None:
        """None if the session may proceed, else the refusal reason."""
        now = time.time()
        mono = time.monotonic()      # idle tracking must not follow wall-clock steps
        jti = str(claims.get("jti") or "")
        sub = str(claims.get("sub") or "")
        iat = int(claims.get("iat") or 0)
        if not jti:
            return "token has no session id"
        if jti in self.revoked_jti:
            return "session revoked"
        nb = self.user_not_before.get(sub)
        if nb is not None and iat < nb:
            return "session revoked"
        last = self._last_seen.get(jti)
        if last is not None and mono - last > self.idle_timeout:
            self.revoked_jti[jti] = int(claims.get("exp") or now + 86400)
            self._last_seen.pop(jti, None)
            return "session idle timeout"
        self._last_seen[jti] = mono
        if len(self._last_seen) > 50_000:
            self._prune(now, mono)
        return None

    def _prune(self, now: float, mono: float) -> None:
        cutoff = mono - self.idle_timeout
        self._last_seen = {k: v for k, v in self._last_seen.items() if v >= cutoff}
        self.revoked_jti = {k: e for k, e in self.revoked_jti.items() if e > now}

    # state updates
    def apply_state(self, state: dict) -> None:
        self.user_not_before.update({k: int(v) for k, v in (state.get("users") or {}).items()})
        self.revoked_jti.update({k: int(v) for k, v in (state.get("jtis") or {}).items()})
        if state.get("idle_timeout"):
            self.idle_timeout = int(state["idle_timeout"])
        self.synced = True


async def sync_from_iam(guard: SessionGuard, base_url_fn, *, attempts: int = 60) -> None:
    """Load the full revocation state from IAM, retrying until IAM is up."""
    for i in range(attempts):
        try:
            base = await base_url_fn()
            if base:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    r = await c.get(base.rstrip("/") + "/internal/sessions/state")
                if r.status_code == 200:
                    guard.apply_state(r.json())
                    log.info("portal.sessions.synced", users=len(guard.user_not_before),
                             tokens=len(guard.revoked_jti), idle_timeout=guard.idle_timeout)
                    return
        except Exception as exc:
            log.warning("portal.sessions.sync_failed", attempt=i + 1, error=str(exc))
        await asyncio.sleep(min(2 + i, 10))


def build_session_consumer(*, url: str, exchange: str, guard: SessionGuard) -> EventConsumer:
    consumer = EventConsumer(url=url, exchange_name=exchange,
                             queue_name="portal.sessions",
                             service_name="consumer-portal-service")

    @consumer.on("iam.session.#")
    async def _on_session(routing_key: str, payload: dict) -> None:
        if routing_key == "iam.session.user_revoked" and payload.get("user_id"):
            guard.user_not_before[str(payload["user_id"])] = int(payload.get("not_before") or time.time())
        elif routing_key == "iam.session.token_revoked" and payload.get("jti"):
            guard.revoked_jti[str(payload["jti"])] = int(payload.get("exp") or time.time() + 86400)
        elif routing_key == "iam.session.policy_changed" and payload.get("idle_timeout"):
            guard.idle_timeout = int(payload["idle_timeout"])
        log.info("portal.sessions.update", routing_key=routing_key,
                 idle_timeout=guard.idle_timeout, revoked_users=len(guard.user_not_before))

    return consumer
