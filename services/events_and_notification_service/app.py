from __future__ import annotations

from fastapi import FastAPI

from cyber_range_core import CoreSettings, create_app, get_logger

import db
from consumer    import build_consumer
from routes      import router as http_router
from ws_gateway  import WSManager, router as ws_router


class Settings(CoreSettings):
    SERVICE_NAME: str = "events-service"
    PORT: int         = 8004

    DATABASE_URL: str = "postgresql+asyncpg://sor:sor@localhost:5432/sor"

    # Token verification; the deployment sets RS256 and the IAM public key.
    JWT_SECRET:    str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER:    str = "cyber-range-iam"

    # The queue we bind to `cyber_range.events`. One-per-service so
    # restarts don't lose backlog.
    EVENTS_QUEUE: str = "events-service.log"


settings = Settings()
log      = get_logger(__name__)


async def _on_startup(_app: FastAPI) -> None:
    if settings.JWT_SECRET == "change-me-in-prod":
        log.warning("events.jwt.default_secret_in_use")

    db.init_engine(settings.DATABASE_URL)
    await db.create_schema()
    log.info("events.db.ready", url=settings.DATABASE_URL.split("@")[-1])

    _app.state.ws_manager = WSManager()


async def _on_shutdown(_app: FastAPI) -> None:
    await db.dispose_engine()


async def _consul_ready() -> bool:
    return isinstance(await app.state.registry.list_services(), dict)


async def _rabbit_ready() -> bool:
    publisher = app.state.publisher
    return await publisher.ensure_ready()


# Build the consumer up front so create_app() can wire it into its lifespan.
ws_manager_placeholder = WSManager()   # replaced inside _on_startup; harmless


# Late-bound consumer factory — needs ws_manager which only exists after
# startup. We pass a wrapper that resolves from app.state on each call.
class _LazyManager:
    def __init__(self): self._inner: WSManager | None = None
    def attach(self, app): self._inner = app.state.ws_manager
    async def fan_out_event(self, **kw):
        if self._inner is None: return
        await self._inner.fan_out_event(**kw)


_lazy = _LazyManager()


async def _attach_consumer(_app: FastAPI) -> None:
    # By now create_app() has set app.state.ws_manager + started the publisher.
    _lazy.attach(_app)


event_consumer = build_consumer(
    url=settings.RABBITMQ_URL,
    exchange=settings.EVENTS_EXCHANGE,
    queue=settings.EVENTS_QUEUE,
    ws_manager=_lazy,
)


app = create_app(
    settings,
    routers=[http_router, ws_router],
    on_startup=[_on_startup, _attach_consumer],
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
