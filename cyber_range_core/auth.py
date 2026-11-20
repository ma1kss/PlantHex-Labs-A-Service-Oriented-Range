from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError

from cyber_range_core.settings import CoreSettings

log = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False)

# Cache for Keycloak JWKS keys (reset per process)
_jwks_cache: dict[str, dict] = {}


@dataclass
class UserContext:
    """What a request handler sees about the authenticated principal."""
    id:       str
    username: str = ""
    email:    str = ""
    roles:    list[str] = field(default_factory=list)
    teams:    list[str] = field(default_factory=list)
    raw:      dict = field(default_factory=dict)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_any_role(self, *roles: str) -> bool:
        return any(r in self.roles for r in roles)


# Settings dependency — populated by create_app() via dependency_overrides

def _get_settings_placeholder() -> CoreSettings:
    # Overridden by create_app() so services don't have to think about it
    raise RuntimeError(
        "CoreSettings dependency not wired. "
        "Did you build your app with cyber_range_core.create_app()?"
    )


# Keycloak JWKS handling

async def _fetch_jwks(jwks_url: str) -> dict:
    if jwks_url in _jwks_cache:
        return _jwks_cache[jwks_url]

    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(jwks_url)
        r.raise_for_status()
        jwks = r.json()

    _jwks_cache[jwks_url] = jwks
    return jwks


async def _validate_jwt(token: str, settings: CoreSettings) -> dict:
    try:
        jwks = await _fetch_jwks(settings.KEYCLOAK_JWKS_URL)
    except Exception as exc:
        log.error("Cannot fetch JWKS: %s", exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Identity provider unavailable")

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown signing key")

        claims = jwt.decode(
            token,
            key,
            algorithms=[key.get("alg", "RS256")],
            audience=settings.KEYCLOAK_AUDIENCE or None,
            issuer=settings.KEYCLOAK_ISSUER or None,
            options={"verify_aud": bool(settings.KEYCLOAK_AUDIENCE)},
        )
        return claims
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}")


def _claims_to_user(claims: dict) -> UserContext:
    """Map Keycloak claims into our UserContext."""
    realm_roles   = claims.get("realm_access", {}).get("roles", []) or []
    client_roles  = []
    for _client, caccess in (claims.get("resource_access") or {}).items():
        client_roles.extend(caccess.get("roles", []) or [])

    return UserContext(
        id=claims.get("sub", ""),
        username=claims.get("preferred_username", ""),
        email=claims.get("email", ""),
        roles=list({*realm_roles, *client_roles}),
        teams=claims.get("groups", []) or [],
        raw=claims,
    )


# FastAPI dependencies

async def require_auth(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    x_player_id: Annotated[str | None, Header(alias="X-Player-Id")] = None,
    settings: Annotated[CoreSettings, Depends(_get_settings_placeholder)] = None,
) -> UserContext:
    """
    Returns a UserContext or raises 401.
    In dev mode (AUTH_ENABLED=false) falls back to X-Player-Id header.
    """
    if not settings.AUTH_ENABLED:
        # Dev stub: trust the header
        player_id = x_player_id or "dev-player"
        user = UserContext(id=player_id, username=player_id, roles=["trainee"])
        request.state.user = user
        return user

    if not creds or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Bearer token")

    claims = await _validate_jwt(creds.credentials, settings)
    user   = _claims_to_user(claims)
    request.state.user = user
    return user


async def optional_auth(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    x_player_id: Annotated[str | None, Header(alias="X-Player-Id")] = None,
    settings: Annotated[CoreSettings, Depends(_get_settings_placeholder)] = None,
) -> UserContext | None:
    """Non-raising version — returns None if unauthenticated."""
    try:
        return await require_auth(request, creds, x_player_id, settings)
    except HTTPException:
        return None


def require_role(*roles: str):
    """Role-check dependency factory. Usage: Depends(require_role('admin'))"""
    async def _checker(user: Annotated[UserContext, Depends(require_auth)]) -> UserContext:
        if not user.has_any_role(*roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"Requires one of roles: {', '.join(roles)}")
        return user
    return _checker
