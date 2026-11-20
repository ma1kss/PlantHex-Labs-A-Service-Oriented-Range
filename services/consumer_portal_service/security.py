from __future__ import annotations

import re

from jose import jwt, JWTError


# (method, path-regex) pairs that bypass edge auth. Paths are the full
# request path as seen by the portal (e.g. "/iam/auth/login").
_PUBLIC_RULES: list[tuple[str, re.Pattern]] = [
    ("POST", re.compile(r"^/iam/auth/login/?$")),
    ("POST", re.compile(r"^/iam/auth/register/?$")),
    # Read-only service catalog used by the dashboard tiles.
    ("GET",  re.compile(r"^/api/services/?$")),
]

# Request path prefixes that are proxied inward and therefore require a token
# (unless matched by a public rule above). Everything else (the SPA static
# assets, the portal's own /health, /ready, /metrics) is served locally and
# is not subject to edge auth.
PROTECTED_PREFIXES = ("/iam/", "/scenario/", "/events/", "/monitoring/",
                      "/data/", "/learning/", "/orchestration/")


def is_public(method: str, path: str) -> bool:
    for m, rx in _PUBLIC_RULES:
        if m == method.upper() and rx.match(path):
            return True
    return False


def requires_edge_auth(method: str, path: str) -> bool:
    """True if the portal should validate a token before forwarding."""
    if is_public(method, path):
        return False
    return path.startswith(PROTECTED_PREFIXES)


def decode_token(token: str, *, secret: str, algorithm: str, issuer: str) -> dict | None:
    """Verified claims of an IAM-issued JWT, or None if invalid/expired."""
    if not token:
        return None
    try:
        return jwt.decode(token, secret, algorithms=[algorithm], issuer=issuer,
                          options={"verify_aud": False})
    except JWTError:
        return None


def is_internal_path(path: str) -> bool:
    """IAM's /internal/* endpoints serve the control plane only."""
    return "/internal/" in path or path.endswith("/internal")


def validate_token(token: str, *, secret: str, algorithm: str, issuer: str) -> bool:
    """Validate an IAM-issued JWT. Returns True iff the signature, issuer and
    expiry all check out. We do not inspect roles here — authorisation (F02)
    stays with IAM and the downstream services."""
    if not token:
        return False
    try:
        jwt.decode(
            token, secret,
            algorithms=[algorithm],
            issuer=issuer,
            options={"verify_aud": False},
        )
        return True
    except JWTError:
        return False


def bearer_from_header(authorization: str | None) -> str:
    """Extract the raw token from an `Authorization: Bearer <token>` header."""
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""
