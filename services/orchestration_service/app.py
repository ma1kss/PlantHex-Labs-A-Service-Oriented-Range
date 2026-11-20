from __future__ import annotations

from fastapi import FastAPI

from cyber_range_core import CoreSettings, create_app, get_logger

import db
from routes import router


class Settings(CoreSettings):
    SERVICE_NAME: str = "orchestration-service"
    PORT: int         = 8009

    DATABASE_URL: str = "postgresql+asyncpg://sor:sor@localhost:5432/sor"

    # Token verification; the deployment sets RS256 and the IAM public key.
    JWT_SECRET:    str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER:    str = "cyber-range-iam"

    # Peers it coordinates (registry preferred; this is the fallback map).
    DOWNSTREAM_FALLBACK: str = (
        "scenario-service=http://sor_scenario:8002,"
        "iam-service=http://sor_iam:8001,"
        "learning-service=http://sor_learning:8008,"
        "monitoring-service=http://sor_monitoring:8005"
    )

    def downstream_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for pair in (self.DOWNSTREAM_FALLBACK or "").split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            name, url = pair.split("=", 1)
            out[name.strip()] = url.strip()
        return out


settings = Settings()
log      = get_logger(__name__)


async def _on_startup(_app: FastAPI) -> None:
    if settings.JWT_SECRET == "change-me-in-prod":
        log.warning("orch.jwt.default_secret_in_use")
    db.init_engine(settings.DATABASE_URL)
    await db.create_schema()
    log.info("orch.ready", peers=settings.downstream_map())


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
