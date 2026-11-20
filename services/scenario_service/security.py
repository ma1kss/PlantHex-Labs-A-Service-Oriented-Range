from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError

from cyber_range_core.auth import UserContext


_bearer = HTTPBearer(auto_error=False)


# Flag hashing

def hash_flag(flag: str) -> str:
    """SHA-256 of the trimmed flag. Stored at rest; never logged."""
    return hashlib.sha256(flag.strip().encode("utf-8")).hexdigest()


def flag_matches(submitted: str, flag_hash: str) -> bool:
    """Constant-time comparison so we don't leak timing info."""
    return hmac.compare_digest(hash_flag(submitted), flag_hash)


# Flag encryption at rest (SEC-005)
# The recoverable copy of a flag (shown to administrators) is stored encrypted
# with Fernet (AES-128-CBC + HMAC-SHA256). FLAG_KEYS_FILE holds one key per
# line, newest first; MultiFernet decrypts with any of them and re-encryption
# at start-up moves every stored flag to the newest key (rotation).

_FLAG_CRYPTO = None
ENC_PREFIX = "enc:"


def init_flag_crypto(keys_file: str) -> bool:
    global _FLAG_CRYPTO
    from pathlib import Path
    if not keys_file or not Path(keys_file).is_file():
        return False
    from cryptography.fernet import Fernet, MultiFernet
    keys = [k.strip() for k in Path(keys_file).read_text().splitlines() if k.strip()]
    _FLAG_CRYPTO = MultiFernet([Fernet(k.encode()) for k in keys]) if keys else None
    return _FLAG_CRYPTO is not None


def encrypt_flag(flag: str) -> str:
    flag = (flag or "").strip()
    if not flag or _FLAG_CRYPTO is None:
        return flag
    return ENC_PREFIX + _FLAG_CRYPTO.encrypt(flag.encode()).decode()


def decrypt_flag(stored: str) -> str:
    if not stored or not stored.startswith(ENC_PREFIX):
        return stored or ""
    if _FLAG_CRYPTO is None:
        return ""
    return _FLAG_CRYPTO.decrypt(stored[len(ENC_PREFIX):].encode()).decode()


def rotate_flag(stored: str) -> str:
    """Plaintext → encrypted; encrypted → re-encrypted with the newest key."""
    if not stored or _FLAG_CRYPTO is None:
        return stored
    if not stored.startswith(ENC_PREFIX):
        return encrypt_flag(stored)
    return ENC_PREFIX + _FLAG_CRYPTO.rotate(stored[len(ENC_PREFIX):].encode()).decode()


# JWT validation

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


def require_role(*roles: str):
    async def _checker(user: Annotated[UserContext, Depends(require_auth_local)]) -> UserContext:
        if not any(r in user.roles for r in roles):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of roles: {', '.join(roles)}",
            )
        return user
    return _checker
