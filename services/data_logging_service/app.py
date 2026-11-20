from __future__ import annotations

from fastapi import FastAPI

from cyber_range_core import CoreSettings, create_app, get_logger

from consumer import build_consumer
from routes import router
from store import RecordStore


class Settings(CoreSettings):
    SERVICE_NAME: str = "data-logging-service"
    PORT: int         = 8007

    # Token verification; the deployment sets RS256 and the IAM public key.
    JWT_SECRET:    str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER:    str = "cyber-range-iam"

    # OpenSearch (comma-separated http URLs).
    OPENSEARCH_HOSTS: str = "http://sor_opensearch:9200"

    # Durable queue bound to cyber_range.events (#) for the capture consumer.
    DATA_QUEUE: str = "data-logging.capture"


settings = Settings()
log      = get_logger(__name__)


# Build the store + the capture consumer up front so create_app can manage
# the consumer's start/stop in its lifespan.
store = RecordStore(settings.OPENSEARCH_HOSTS)
event_consumer = build_consumer(
    url=settings.RABBITMQ_URL,
    exchange=settings.EVENTS_EXCHANGE,
    queue=settings.DATA_QUEUE,
    store=store,
)


async def _on_startup(_app: FastAPI) -> None:
    if settings.JWT_SECRET == "change-me-in-prod":
        log.warning("data.jwt.default_secret_in_use")
    _app.state.store = store
    await store.ensure_index()
    try:
        await store.init_chain()        # tamper-evident archive (SEC-012)
    except Exception as exc:
        log.warning("data.chain.init_failed", error=str(exc))
    log.info("data.ready", hosts=settings.OPENSEARCH_HOSTS)


async def _on_shutdown(_app: FastAPI) -> None:
    await store.close()


async def _consul_ready() -> bool:
    return isinstance(await app.state.registry.list_services(), dict)


async def _rabbit_ready() -> bool:
    publisher = app.state.publisher
    return await publisher.ensure_ready()


async def _store_ready() -> bool:
    return await store.reachable()


app = create_app(
    settings,
    routers=[router],
    on_startup=[_on_startup],
    on_shutdown=[_on_shutdown],
    event_consumer=event_consumer,
    readiness_checks={
        "consul":     _consul_ready,
        "rabbitmq":   _rabbit_ready,
        "opensearch": _store_ready,
    },
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
