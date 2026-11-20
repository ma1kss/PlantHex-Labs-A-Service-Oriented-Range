from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cyber_range_core import UserContext, get_logger

import schemas
from db import User, AccessGrant, PlatformSetting, RevokedToken, SessionRevocation, get_sessionmaker
from security import (
    hash_password,
    verify_password,
    issue_token,

    require_auth_local,
    optional_auth_local,
    require_role,
)


# Public self-signup may only create these roles. Anything elevated needs an
# existing admin token — keeps the open endpoint from becoming a backdoor.
PUBLIC_REGISTRATION_ROLES = {"player"}

# Roles an admin can assign via /users/{id}/roles.
ASSIGNABLE_ROLES = {"player", "instructor", "admin"}


log    = get_logger(__name__)
router = APIRouter()


# /auth

async def _registration_secret(request: Request) -> str:
    async with get_sessionmaker()() as s:
        row = await s.get(PlatformSetting, "registration_secret")
    return row.value if row else request.app.state.settings.REGISTRATION_SECRET


@router.post("/auth/register",
             status_code=status.HTTP_201_CREATED,
             response_model=schemas.UserOut,
             summary="Register a new user")
async def register(body: schemas.RegisterRequest,
                   request: Request,
                   caller: UserContext | None = Depends(optional_auth_local)):
    requested_roles = list(body.roles or ["player"])
    privileged = set(requested_roles) - PUBLIC_REGISTRATION_ROLES
    if privileged and (caller is None or not caller.has_role("admin")):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Registering users with elevated roles "
            f"({', '.join(sorted(privileged))}) requires an admin token",
        )

    # Public self-signup needs the current registration secret. Admin-token
    # registrations skip it; already-registered users are never re-checked.
    if caller is None or not caller.has_role("admin"):
        if body.secret != await _registration_secret(request):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Invalid registration secret.")

    async with get_sessionmaker()() as s:
        user = User(
            username=body.username,
            email=body.email or "",
            password_hash=hash_password(body.password),
            roles=requested_roles,
        )
        s.add(user)
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
        await s.refresh(user)

    await request.app.state.publisher.publish(
        "iam.user.registered",
        {"user_id": user.id, "username": user.username, "roles": user.roles},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.user.registered", user_id=user.id, username=user.username)

    return schemas.UserOut(
        id=user.id, username=user.username, email=user.email,
        roles=user.roles, created_at=user.created_at,
    )


@router.post("/auth/login",
             response_model=schemas.TokenResponse,
             summary="Exchange credentials for a JWT")
async def login(body: schemas.LoginRequest, request: Request):
    settings = request.app.state.settings
    guard    = request.app.state.login_guard
    pub      = request.app.state.publisher
    source   = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")

    remaining = guard.locked(body.username)
    if remaining:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Account temporarily locked after repeated failed logins; "
                            f"retry in {int(remaining) + 1} s.")

    async with get_sessionmaker()() as s:
        user = (await s.execute(
            select(User).where(User.username == body.username)
        )).scalar_one_or_none()

    async def _fail(reason: str, detail: str, code: int = status.HTTP_401_UNAUTHORIZED):
        n, just_locked = guard.failure(body.username)
        await pub.publish("iam.login.failed",
                          {"username": body.username, "reason": reason,
                           "failures_in_window": n, "source": source},
                          correlation_id=request.state.correlation_id)
        if just_locked:
            # Security alert (SEC-014): surfaced to staff via the event feed,
            # archived, and listed by the monitoring service.
            await pub.publish("security.alert.login_lockout",
                              {"severity": "warning", "username": body.username,
                               "failures": n, "source": source,
                               "lock_seconds": guard.lock,
                               "message": f"{n} failed logins for '{body.username}' — account locked"},
                              correlation_id=request.state.correlation_id)
            log.warning("iam.login.lockout", username=body.username, source=source)
        raise HTTPException(code, detail)

    if user is None or not verify_password(body.password, user.password_hash):
        await _fail("bad_credentials", "Invalid username or password")

    amr = ["pwd"]
    guard.success(body.username)
    token, ttl = issue_token(
        user_id=user.id,
        username=user.username,
        email=user.email,
        roles=user.roles,
        secret=settings.signing_key,
        algorithm=settings.JWT_ALGORITHM,
        issuer=settings.JWT_ISSUER,
        ttl_seconds=settings.JWT_TTL_SECONDS,
        amr=amr,
    )

    await pub.publish(
        "iam.user.login",
        {"user_id": user.id, "username": user.username, "amr": amr, "source": source},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.user.login", user_id=user.id, username=user.username, amr=amr)

    return schemas.TokenResponse(access_token=token, expires_in=ttl)


@router.get("/auth/validate",
            response_model=schemas.ValidateResponse,
            summary="Validate a Bearer token and return UserContext")
async def validate(user: UserContext = Depends(require_auth_local)):
    return schemas.ValidateResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        roles=user.roles,
        teams=user.teams,
    )


@router.post("/auth/change-password",
             status_code=status.HTTP_204_NO_CONTENT,
             summary="Change your own password (verifies current password first)")
async def change_password(body: schemas.ChangePasswordRequest,
                          request: Request,
                          caller: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        user = await s.get(User, caller.id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
        if body.current_password == body.new_password:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "New password must differ from the current one")
        user.password_hash = hash_password(body.new_password)
        await s.commit()
    await _revoke_user_sessions(request, caller.id, reason="password_changed")

    await request.app.state.publisher.publish(
        "iam.user.password_changed",
        {"user_id": caller.id, "username": caller.username},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.user.password_changed", user_id=caller.id, username=caller.username)
    return None


# /profile (self-service, any signed-in user)

def _profile_out(u: User) -> schemas.ProfileOut:
    return schemas.ProfileOut(
        id=u.id, username=u.username, email=u.email, roles=u.roles,
        display_name=u.display_name or "", bio=u.bio or "",
        avatar=u.avatar or "", created_at=u.created_at,
    )


@router.get("/profile",
            response_model=schemas.ProfileOut,
            summary="Get my own profile")
async def get_profile(caller: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        user = await s.get(User, caller.id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        return _profile_out(user)


@router.put("/profile",
            response_model=schemas.ProfileOut,
            summary="Update my own profile (display name, email, bio, avatar)")
async def update_profile(body: schemas.ProfileUpdate,
                         request: Request,
                         caller: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        user = await s.get(User, caller.id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        if body.display_name is not None: user.display_name = body.display_name.strip()
        if body.email        is not None: user.email        = body.email.strip()
        if body.bio          is not None: user.bio          = body.bio.strip()
        if body.avatar       is not None: user.avatar       = body.avatar.strip()
        await s.commit()
        await s.refresh(user)
        out = _profile_out(user)

    await request.app.state.publisher.publish(
        "iam.user.profile_updated",
        {"user_id": caller.id, "username": caller.username},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.user.profile_updated", user_id=caller.id)
    return out


# /profiles (viewing other users, role-gated)

# Visibility ladder: players see players, instructors see instructors and
# players, admins see everyone. A user's rank is their highest role.
_ROLE_RANK = {"player": 0, "instructor": 1, "admin": 2}


def _rank(roles: list[str]) -> int:
    return max((_ROLE_RANK.get(r, 0) for r in roles or []), default=0)


@router.get("/profiles/{user_id}",
            response_model=schemas.PublicProfileOut,
            summary="View another user's profile (role-gated visibility)")
async def get_public_profile(user_id: str,
                             caller: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        user = await s.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id != caller.id and _rank(user.roles) > _rank(caller.roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You are not allowed to view this profile.")
    return schemas.PublicProfileOut(
        id=user.id, username=user.username, roles=user.roles,
        display_name=user.display_name or "", bio=user.bio or "",
        avatar=user.avatar or "", created_at=user.created_at,
    )


# /settings (admin platform configuration)

@router.get("/settings/registration-secret",
            response_model=schemas.RegistrationSecretOut,
            summary="View the current signup secret (admin only)")
async def get_registration_secret(request: Request,
                                  _: UserContext = Depends(require_role("admin"))):
    return schemas.RegistrationSecretOut(secret=await _registration_secret(request))


@router.put("/settings/registration-secret",
            response_model=schemas.RegistrationSecretOut,
            summary="Change the signup secret (admin only)")
async def set_registration_secret(body: schemas.RegistrationSecretUpdate,
                                  request: Request,
                                  admin: UserContext = Depends(require_role("admin"))):
    async with get_sessionmaker()() as s:
        row = await s.get(PlatformSetting, "registration_secret")
        if row is None:
            row = PlatformSetting(key="registration_secret")
            s.add(row)
        row.value = body.secret
        await s.commit()

    await request.app.state.publisher.publish(
        "iam.settings.registration_secret_changed",
        {"changed_by": admin.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.settings.registration_secret_changed", changed_by=admin.id)
    return schemas.RegistrationSecretOut(secret=body.secret)


# /access (F02 grants — used by the Orchestration launch workflow)

def _is_staff(u: UserContext) -> bool:
    return u.has_role("admin") or u.has_role("instructor")


@router.post("/access/grant",
             response_model=schemas.AccessGrantOut,
             status_code=status.HTTP_201_CREATED,
             summary="Grant access to a launched resource (F02)")
async def grant_access(body: schemas.AccessGrantRequest,
                       request: Request,
                       caller: UserContext = Depends(require_auth_local)):
    target = body.user_id or caller.id
    if body.user_id and body.user_id != caller.id and not _is_staff(caller):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Granting access for another user requires staff role")
    async with get_sessionmaker()() as s:
        grant = AccessGrant(user_id=target, resource=body.resource,
                            session_id=body.session_id, active=True)
        s.add(grant)
        await s.commit()
        await s.refresh(grant)
        out = schemas.AccessGrantOut(grant_id=grant.id, user_id=grant.user_id,
                                     resource=grant.resource, session_id=grant.session_id,
                                     active=grant.active)

    await request.app.state.publisher.publish(
        "iam.access.granted",
        {"grant_id": out.grant_id, "user_id": out.user_id,
         "resource": out.resource, "session_id": out.session_id},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.access.granted", grant_id=out.grant_id, user_id=out.user_id,
             resource=out.resource)
    return out


@router.post("/access/revoke",
             response_model=schemas.AccessGrantOut,
             summary="Revoke an access grant (owner or staff)")
async def revoke_access(body: schemas.AccessRevokeRequest,
                        request: Request,
                        caller: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        grant = await s.get(AccessGrant, body.grant_id)
        if grant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Grant not found")
        if grant.user_id != caller.id and not _is_staff(caller):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your grant")
        if grant.active:
            grant.active = False
            grant.revoked_at = datetime.now(timezone.utc)
            await s.commit()
            await s.refresh(grant)
        out = schemas.AccessGrantOut(grant_id=grant.id, user_id=grant.user_id,
                                     resource=grant.resource, session_id=grant.session_id,
                                     active=grant.active)

    await request.app.state.publisher.publish(
        "iam.access.revoked",
        {"grant_id": out.grant_id, "user_id": out.user_id, "resource": out.resource},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.access.revoked", grant_id=out.grant_id, user_id=out.user_id)
    return out


# /users (admin)

@router.get("/users",
            response_model=list[schemas.UserOut],
            summary="List users (admin sees all, instructors don't see admins)")
async def list_users(caller: UserContext = Depends(require_role("admin", "instructor"))):
    async with get_sessionmaker()() as s:
        users = (await s.execute(select(User).order_by(User.created_at))).scalars().all()
    if not caller.has_role("admin"):
        users = [u for u in users if "admin" not in (u.roles or [])]
    return [
        schemas.UserOut(
            id=u.id, username=u.username, email=u.email,
            roles=u.roles, created_at=u.created_at,
        )
        for u in users
    ]


@router.delete("/users/{user_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a user (admin only)")
async def delete_user(user_id: str,
                      request: Request,
                      admin: UserContext = Depends(require_role("admin"))):
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Admins cannot delete their own account")

    async with get_sessionmaker()() as s:
        target = await s.get(User, user_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        if "admin" in (target.roles or []):
            others = (await s.execute(
                select(User).where(User.id != target.id)
            )).scalars().all()
            other_admins = sum(1 for u in others if "admin" in (u.roles or []))
            if other_admins == 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Cannot delete the last admin account",
                )

        await s.delete(target)
        await s.commit()

    await _revoke_user_sessions(request, user_id, reason="user_deleted")
    await request.app.state.publisher.publish(
        "iam.user.deleted",
        {"user_id": user_id, "deleted_by": admin.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.user.deleted", user_id=user_id, deleted_by=admin.id)
    return None


@router.put("/users/{user_id}/roles",
            response_model=schemas.UserOut,
            summary="Promote / demote a user (admin only)")
async def update_user_roles(user_id: str,
                            body: schemas.UpdateRolesRequest,
                            request: Request,
                            admin: UserContext = Depends(require_role("admin"))):
    requested = list(dict.fromkeys(r.strip() for r in (body.roles or []) if r.strip()))
    if not requested:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one role is required")

    unknown = set(requested) - ASSIGNABLE_ROLES
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown role(s): {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(ASSIGNABLE_ROLES))}",
        )

    async with get_sessionmaker()() as s:
        target = await s.get(User, user_id)
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

        was_admin = "admin" in (target.roles or [])
        will_be_admin = "admin" in requested

        if was_admin and not will_be_admin:
            # Demoting an admin: make sure at least one other admin remains.
            others = (await s.execute(
                select(User).where(User.id != target.id)
            )).scalars().all()
            other_admins = sum(1 for u in others if "admin" in (u.roles or []))
            if other_admins == 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Cannot demote the last admin account",
                )

        target.roles = requested
        await s.commit()
        await s.refresh(target)
        out = schemas.UserOut(
            id=target.id, username=target.username, email=target.email,
            roles=target.roles, created_at=target.created_at,
        )

    await _revoke_user_sessions(request, user_id, reason="roles_changed")
    await request.app.state.publisher.publish(
        "iam.user.roles_updated",
        {"user_id": user_id, "roles": requested, "updated_by": admin.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.user.roles_updated",
             user_id=user_id, roles=requested, updated_by=admin.id)
    return out


# Sessions (SEC-022): revocation + idle-timeout policy
#
# Tokens are verified locally by every service, so revocation is enforced at
# the platform edge (Consumer & Portal), the only way in from outside. IAM is
# the source of truth: it records revocations, announces them on the bus
# (iam.session.*) and serves the full state at /internal/sessions/state, which
# the edge reads at start-up. The edge never forwards /internal/* paths.

async def _setting(key: str, default: str) -> str:
    async with get_sessionmaker()() as s:
        row = await s.get(PlatformSetting, key)
    return row.value if row is not None else default


async def _put_setting(key: str, value: str) -> None:
    async with get_sessionmaker()() as s:
        row = await s.get(PlatformSetting, key)
        if row is None:
            row = PlatformSetting(key=key)
            s.add(row)
        row.value = value
        await s.commit()


async def _idle_timeout(request: Request) -> int:
    return int(await _setting("session_idle_timeout",
                              str(request.app.state.settings.SESSION_IDLE_TIMEOUT_SECONDS)))


async def _revoke_user_sessions(request: Request, user_id: str, *, reason: str) -> int:
    not_before = int(datetime.now(timezone.utc).timestamp())
    async with get_sessionmaker()() as s:
        row = await s.get(SessionRevocation, user_id)
        if row is None:
            row = SessionRevocation(user_id=user_id)
            s.add(row)
        row.not_before = datetime.fromtimestamp(not_before, timezone.utc)
        row.reason = reason[:128]
        await s.commit()
    await request.app.state.publisher.publish(
        "iam.session.user_revoked",
        {"user_id": user_id, "not_before": not_before, "reason": reason},
        correlation_id=request.state.correlation_id,
    )
    log.info("iam.session.user_revoked", user_id=user_id, reason=reason)
    return not_before


@router.post("/auth/logout",
             status_code=status.HTTP_204_NO_CONTENT,
             summary="Revoke the token used for this request")
async def logout(request: Request, caller: UserContext = Depends(require_auth_local)):
    jti = str(caller.raw.get("jti") or "")
    exp = datetime.fromtimestamp(int(caller.raw.get("exp") or 0), timezone.utc)
    if jti:
        async with get_sessionmaker()() as s:
            if await s.get(RevokedToken, jti) is None:
                s.add(RevokedToken(jti=jti, user_id=caller.id, expires_at=exp))
                await s.commit()
        await request.app.state.publisher.publish(
            "iam.session.token_revoked",
            {"jti": jti, "user_id": caller.id, "exp": int(exp.timestamp())},
            correlation_id=request.state.correlation_id,
        )
    return None


@router.post("/users/{user_id}/sessions/revoke",
             response_model=schemas.SessionRevokeOut,
             summary="Immediately revoke every session of a user (admin only)")
async def revoke_sessions(user_id: str, request: Request,
                          admin: UserContext = Depends(require_role("admin"))):
    async with get_sessionmaker()() as s:
        if await s.get(User, user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    nb = await _revoke_user_sessions(request, user_id, reason=f"admin:{admin.id}")
    return schemas.SessionRevokeOut(user_id=user_id, not_before=nb)


@router.get("/settings/security",
            response_model=schemas.SecuritySettings,
            summary="Session policy (admin only)")
async def get_security_settings(request: Request,
                                _: UserContext = Depends(require_role("admin"))):
    return schemas.SecuritySettings(
        session_idle_timeout_seconds=await _idle_timeout(request),
        token_ttl_seconds=request.app.state.settings.JWT_TTL_SECONDS,
    )


@router.put("/settings/security",
            response_model=schemas.SecuritySettings,
            summary="Change the session idle timeout (admin only)")
async def put_security_settings(body: schemas.SecuritySettingsUpdate, request: Request,
                                admin: UserContext = Depends(require_role("admin"))):
    if body.session_idle_timeout_seconds is not None:
        await _put_setting("session_idle_timeout", str(body.session_idle_timeout_seconds))
    out = schemas.SecuritySettings(
        session_idle_timeout_seconds=await _idle_timeout(request),
        token_ttl_seconds=request.app.state.settings.JWT_TTL_SECONDS,
    )
    await request.app.state.publisher.publish(
        "iam.session.policy_changed",
        {"idle_timeout": out.session_idle_timeout_seconds, "changed_by": admin.id},
        correlation_id=request.state.correlation_id,
    )
    return out


@router.get("/internal/sessions/state",
            summary="Revocation state for the edge (sor_net only; never proxied)")
async def sessions_state(request: Request) -> dict:
    now = datetime.now(timezone.utc)
    async with get_sessionmaker()() as s:
        users = (await s.execute(
            select(SessionRevocation.user_id, SessionRevocation.not_before)
        )).all()
        toks = (await s.execute(
            select(RevokedToken).where(RevokedToken.expires_at > now)
        )).scalars().all()
    return {
        "users": {u: int(t.timestamp()) for u, t in users},
        "jtis":  {t.jti: int(t.expires_at.timestamp()) for t in toks},
        "idle_timeout": await _idle_timeout(request),
    }
