from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
from passlib.context import CryptContext

from cyber_range_core.auth import UserContext


_pwd    = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    return _pwd.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _pwd.verify(pw, hashed)
    except Exception:
        return False


def issue_token(
    *,
    user_id: str,
    username: str,
    email: str,
    roles: list[str],
    secret: str,
    algorithm: str,
    issuer: str,
    ttl_seconds: int,
    amr: list[str] | None = None,
) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    claims = {
        "jti":                str(uuid.uuid4()),   # lets one token be revoked (SEC-022)
        "amr":                amr or ["pwd"],      # authentication methods used
        "sub":                user_id,
        "preferred_username": username,
        "email":              email,
        "realm_access":       {"roles": roles},
        "iss":                issuer,
        "iat":                int(now.timestamp()),
        "exp":                int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=algorithm), ttl_seconds


def _decode(token: str, *, secret: str, algorithm: str, issuer: str) -> dict:
    return jwt.decode(
        token, secret,
        algorithms=[algorithm],
        issuer=issuer,
        options={"verify_aud": False},
    )


async def require_auth_local(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> UserContext:
    if not creds or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Bearer token")

    settings = request.app.state.settings
    try:
        claims = _decode(
            creds.credentials,
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            issuer=settings.JWT_ISSUER,
        )
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}")

    user = UserContext(
        id=claims.get("sub", ""),
        username=claims.get("preferred_username", ""),
        email=claims.get("email", ""),
        roles=(claims.get("realm_access") or {}).get("roles", []) or [],
        raw=claims,
    )
    request.state.user = user
    return user


async def optional_auth_local(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> UserContext | None:
    if not creds or not creds.credentials:
        return None
    try:
        return await require_auth_local(request, creds)
    except HTTPException:
        return None


def require_role(*roles: str):
    async def _checker(user: Annotated[UserContext, Depends(require_auth_local)]) -> UserContext:
        if not user.has_any_role(*roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of roles: {', '.join(roles)}",
            )
        return user
    return _checker


# Login throttling / lockout (SR-FR-QoS-39 prevention, SEC-014)

class LoginGuard:
    """In-memory failed-login tracker. After `max_failures` failures for one
    username within `window` seconds the account is locked for `lock` seconds."""

    def __init__(self, max_failures: int = 5, window: int = 300, lock: int = 300):
        self.max_failures, self.window, self.lock = max_failures, window, lock
        self._fails: dict[str, deque] = {}
        self._locked_until: dict[str, float] = {}

    def locked(self, username: str) -> float:
        until = self._locked_until.get(username.lower(), 0.0)
        return max(0.0, until - time.time())

    def failure(self, username: str) -> tuple[int, bool]:
        """Record a failure; returns (failures in window, just_locked)."""
        u, now = username.lower(), time.time()
        q = self._fails.setdefault(u, deque())
        q.append(now)
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_failures and self.locked(u) == 0:
            self._locked_until[u] = now + self.lock
            q.clear()
            return self.max_failures, True
        return len(q), False

    def success(self, username: str) -> None:
        self._fails.pop(username.lower(), None)

    def unlock(self, username: str) -> None:
        self._locked_until.pop(username.lower(), None)
        self._fails.pop(username.lower(), None)

