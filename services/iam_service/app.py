from __future__ import annotations

from fastapi import FastAPI

from cyber_range_core import CoreSettings, create_app, get_logger

import db
from routes import router
from security import hash_password, LoginGuard


class Settings(CoreSettings):
    SERVICE_NAME: str = "iam-service"
    PORT: int         = 8001

    DATABASE_URL: str = "postgresql+asyncpg://sor:sor@localhost:5432/sor"

    JWT_SECRET:      str = "change-me-in-prod"
    JWT_ALGORITHM:   str = "HS256"
    JWT_ISSUER:      str = "cyber-range-iam"
    JWT_TTL_SECONDS: int = 3600
    # RS256 signing key (SEC-008): only IAM holds the private key; every other
    # service verifies with the public key (CoreSettings.JWT_PUBLIC_KEY_FILE).
    JWT_PRIVATE_KEY_FILE: str = ""
    # Session policy defaults (SEC-022); admins change them at runtime via
    # PUT /settings/security, which is stored in platform_settings.
    SESSION_IDLE_TIMEOUT_SECONDS: int = 1800
    # Login throttling (SR-FR-QoS-39): lock an account after N failures.
    LOGIN_MAX_FAILURES:   int = 5
    LOGIN_WINDOW_SECONDS: int = 300
    LOGIN_LOCK_SECONDS:   int = 300

    @property
    def signing_key(self) -> str:
        from pathlib import Path
        if self.JWT_PRIVATE_KEY_FILE and Path(self.JWT_PRIVATE_KEY_FILE).is_file():
            return Path(self.JWT_PRIVATE_KEY_FILE).read_text()
        return self.JWT_SECRET

    BOOTSTRAP_ADMIN_USERNAME: str = "charon"
    BOOTSTRAP_ADMIN_PASSWORD: str = "charon"

    # Initial signup secret — seeded into platform_settings on first start;
    # admins change it later via PUT /settings/registration-secret.
    REGISTRATION_SECRET: str = "PxL33980"


settings = Settings()
log      = get_logger(__name__)


async def _seed_bootstrap_admin() -> None:
    """Create the first admin user only if the users table is empty."""
    from sqlalchemy import select, func
    from db import User

    async with db.get_sessionmaker()() as s:
        empty = (await s.execute(select(func.count()).select_from(User))).scalar_one() == 0
        if empty and settings.BOOTSTRAP_ADMIN_PASSWORD:
            s.add(User(
                username=settings.BOOTSTRAP_ADMIN_USERNAME,
                email="",
                password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
                roles=["admin"],
            ))
            await s.commit()
            log.info("iam.bootstrap.admin_created",
                     username=settings.BOOTSTRAP_ADMIN_USERNAME)


async def _seed_registration_secret() -> None:
    """Seed the signup secret once; afterwards the stored value is the truth."""
    from db import PlatformSetting

    async with db.get_sessionmaker()() as s:
        if await s.get(PlatformSetting, "registration_secret") is None:
            s.add(PlatformSetting(key="registration_secret",
                                  value=settings.REGISTRATION_SECRET))
            await s.commit()
            log.info("iam.bootstrap.registration_secret_seeded")


async def _on_startup(_app: FastAPI) -> None:
    if settings.JWT_ALGORITHM.startswith("HS") and settings.JWT_SECRET == "change-me-in-prod":
        log.warning("iam.jwt.default_secret_in_use")
    _app.state.login_guard = LoginGuard(settings.LOGIN_MAX_FAILURES,
                                        settings.LOGIN_WINDOW_SECONDS,
                                        settings.LOGIN_LOCK_SECONDS)
    if settings.BOOTSTRAP_ADMIN_PASSWORD in ("admin", "charon", "password", ""):
        log.warning("iam.bootstrap.weak_admin_password")

    db.init_engine(settings.DATABASE_URL)
    await db.create_schema()
    log.info("iam.db.ready", url=settings.DATABASE_URL.split("@")[-1])

    await _seed_bootstrap_admin()
    await _seed_registration_secret()


async def _on_shutdown(_app: FastAPI) -> None:
    await db.dispose_engine()


async def _consul_ready() -> bool:
    return isinstance(await app.state.registry.list_services(), dict)


async def _rabbit_ready() -> bool:
    publisher = app.state.publisher
    return await publisher.ensure_ready()


app = create_app(
    settings,
    routers=[router],
    on_startup=[_on_startup],
    on_shutdown=[_on_shutdown],
    readiness_checks={
        "consul":   _consul_ready,
        "rabbitmq": _rabbit_ready,
        "postgres": db.ping,
    },
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
