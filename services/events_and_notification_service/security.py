from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError

from cyber_range_core.auth import UserContext


_bearer = HTTPBearer(auto_error=False)


def _decode(token: str, *, secret: str, algorithm: str, issuer: str) -> dict:
    return jwt.decode(
        token, secret,
        algorithms=[algorithm],
        issuer=issuer,
        options={"verify_aud": False},
    )


def claims_to_user(claims: dict) -> UserContext:
    return UserContext(
        id=claims.get("sub", ""),
        username=claims.get("preferred_username", ""),
        email=claims.get("email", ""),
        roles=(claims.get("realm_access") or {}).get("roles", []) or [],
        raw=claims,
    )


def decode_token_str(token: str, settings) -> UserContext:
    """Used by the WebSocket endpoint — raises ValueError on invalid token."""
    try:
        claims = _decode(
            token,
            secret=settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
            issuer=settings.JWT_ISSUER,
        )
    except JWTError as exc:
        raise ValueError(str(exc)) from exc
    return claims_to_user(claims)


async def require_auth_local(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> UserContext:
    if not creds or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Bearer token")

    settings = request.app.state.settings
    try:
        user = decode_token_str(creds.credentials, settings)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}")

    request.state.user = user
    return user


def require_role(*roles: str):
    async def _checker(user: Annotated[UserContext, Depends(require_auth_local)]) -> UserContext:
        if not any(r in user.roles for r in roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of roles: {', '.join(roles)}",
            )
        return user
    return _checker
