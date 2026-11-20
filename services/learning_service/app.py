from __future__ import annotations

from fastapi import FastAPI

from cyber_range_core import CoreSettings, create_app, get_logger

import db
from consumer import build_consumer
from routes import router


class Settings(CoreSettings):
    SERVICE_NAME: str = "learning-service"
    PORT: int         = 8008

    DATABASE_URL: str = "postgresql+asyncpg://sor:sor@localhost:5432/sor"

    # Token verification; the deployment sets RS256 and the IAM public key.
    JWT_SECRET:    str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER:    str = "cyber-range-iam"

    # CTF points policy (by difficulty).
    POINTS_EASY:    int = 10
    POINTS_MEDIUM:  int = 20
    POINTS_HARD:    int = 30
    POINTS_DEFAULT: int = 10   # unknown / custom difficulty

    # Durable queue bound to scenario.flag.submitted for the scoring consumer.
    LEARNING_QUEUE: str = "learning-service.scoring"

    def points_policy(self) -> dict[str, int]:
        return {
            "easy":    self.POINTS_EASY,
            "medium":  self.POINTS_MEDIUM,
            "hard":    self.POINTS_HARD,
            "default": self.POINTS_DEFAULT,
        }


settings = Settings()
log      = get_logger(__name__)

# Holder so the consumer (built before the app) can reach the live publisher.
_publisher_holder: dict = {"pub": None}


event_consumer = build_consumer(
    url=settings.RABBITMQ_URL,
    exchange=settings.EVENTS_EXCHANGE,
    queue=settings.LEARNING_QUEUE,
    policy=settings.points_policy(),
    publisher_ref=lambda: _publisher_holder["pub"],
)


async def _on_startup(_app: FastAPI) -> None:
    if settings.JWT_SECRET == "change-me-in-prod":
        log.warning("learning.jwt.default_secret_in_use")
    db.init_engine(settings.DATABASE_URL)
    await db.create_schema()
    _app.state.points_policy = settings.points_policy()
    _publisher_holder["pub"] = _app.state.publisher
    log.info("learning.ready", policy=settings.points_policy())


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
    event_consumer=event_consumer,
    readiness_checks={
        "consul":   _consul_ready,
        "rabbitmq": _rabbit_ready,
        "postgres": db.ping,
    },
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
