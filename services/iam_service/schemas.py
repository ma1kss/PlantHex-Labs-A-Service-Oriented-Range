from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str       = Field(..., min_length=3, max_length=64)
    password: str       = Field(..., min_length=6, max_length=128)
    email:    str       = ""
    roles:    list[str] = Field(default_factory=lambda: ["player"])
    # Signup secret — required for public self-registration.
    secret:   str       = Field("", max_length=128)


class RegistrationSecretOut(BaseModel):
    secret: str


class RegistrationSecretUpdate(BaseModel):
    secret: str = Field(..., min_length=4, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int


class UserOut(BaseModel):
    id:         str
    username:   str
    email:      str
    roles:      list[str]
    created_at: datetime


class ValidateResponse(BaseModel):
    id:       str
    username: str
    email:    str
    roles:    list[str]
    teams:    list[str] = []


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password:     str = Field(..., min_length=6, max_length=128)


class UpdateRolesRequest(BaseModel):
    roles: list[str] = Field(..., min_length=1)


# Profile (F16)

class ProfileOut(BaseModel):
    id:           str
    username:     str
    email:        str
    roles:        list[str]
    display_name: str
    bio:          str
    avatar:       str
    created_at:   datetime


class PublicProfileOut(BaseModel):
    """Another user's profile — no email, no editable-only internals."""
    id:           str
    username:     str
    roles:        list[str]
    display_name: str
    bio:          str
    avatar:       str
    created_at:   datetime


class ProfileUpdate(BaseModel):
    """All fields optional — only the ones provided are updated. Username and
    roles are deliberately not editable here (identity / authz stay with IAM)."""
    display_name: str | None = Field(None, max_length=80)
    email:        str | None = Field(None, max_length=255)
    bio:          str | None = Field(None, max_length=2000)
    avatar:       str | None = Field(None, max_length=64)


# Access grants (F02)

class AccessGrantRequest(BaseModel):
    resource:   str = Field("", max_length=128)   # e.g. challenge id
    session_id: str = Field("", max_length=36)
    # Optional: grant on behalf of another user (staff only).
    user_id:    str | None = Field(None, max_length=36)


class AccessRevokeRequest(BaseModel):
    grant_id: str = Field(..., min_length=1, max_length=36)


class AccessGrantOut(BaseModel):
    grant_id:   str
    user_id:    str
    resource:   str
    session_id: str
    active:     bool


class SessionRevokeOut(BaseModel):
    user_id:    str
    not_before: int


class SecuritySettings(BaseModel):
    session_idle_timeout_seconds: int
    token_ttl_seconds:            int


class SecuritySettingsUpdate(BaseModel):
    session_idle_timeout_seconds: int | None = Field(None, ge=5, le=86400)
