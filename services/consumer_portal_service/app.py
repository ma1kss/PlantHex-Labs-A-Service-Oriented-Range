from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cyber_range_core import CoreSettings, create_app, get_logger

import asyncio

from gateway import router as gateway_router
from ws_proxy import events_ws_proxy
from sessions import SessionGuard, build_session_consumer, sync_from_iam


WEB_DIR = Path(__file__).parent / "web"


class Settings(CoreSettings):
    SERVICE_NAME: str = "consumer-portal-service"
    PORT: int         = 8006

    # Shared with iam-service so the edge can validate IAM-issued JWTs.
    JWT_SECRET:    str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER:    str = "cyber-range-iam"
    # Default idle timeout until IAM's configured value has been loaded.
    SESSION_IDLE_TIMEOUT_SECONDS: int = 1800

    # TLS for the web edge comes from CoreSettings (SSL_CERTFILE/SSL_KEYFILE):
    # when both PEM files exist the portal serves HTTPS (and wss:// for the
    # events socket), and registration advertises https to Consul/Prometheus.

    # Static fallback endpoints used when the registry can't resolve a
    # service (comma-separated name=url pairs). The registry is preferred.
    DOWNSTREAM_FALLBACK: str = (
        "iam-service=http://sor_iam:8001,"
        "scenario-service=http://sor_scenario:8002,"
        "events-service=http://sor_events:8004,"
        "monitoring-service=http://sor_monitoring:8005,"
        "data-logging-service=http://sor_data:8007,"
        "learning-service=http://sor_learning:8008,"
        "orchestration-service=http://sor_orchestration:8009"
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
session_guard = SessionGuard(settings.SESSION_IDLE_TIMEOUT_SECONDS)


async def _on_startup(_app: FastAPI) -> None:
    # One shared async client for all proxied calls. No total timeout so
    # streaming responses (build console, downloads) aren't cut off; a
    # connect timeout still guards against a dead upstream.
    _app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(None, connect=5.0),
        follow_redirects=False,
    )
    _app.state.sessions = session_guard

    async def _iam_base():
        try:
            ep = await _app.state.registry.get_one("iam-service")
            if ep is not None:
                return ep.base_url
        except Exception:
            pass
        return settings.downstream_map().get("iam-service")
    _app.state.sessions_sync = asyncio.create_task(sync_from_iam(session_guard, _iam_base))
    log.info("portal.ready", web_dir=str(WEB_DIR), static=WEB_DIR.is_dir())


async def _on_shutdown(_app: FastAPI) -> None:
    client = getattr(_app.state, "http", None)
    if client is not None:
        await client.aclose()


async def _consul_ready() -> bool:
    return isinstance(await app.state.registry.list_services(), dict)


async def _web_ready() -> bool:
    return (WEB_DIR / "index.html").is_file()


async def _sessions_ready() -> bool:
    return session_guard.synced


app = create_app(
    settings,
    routers=[gateway_router],
    on_startup=[_on_startup],
    on_shutdown=[_on_shutdown],
    event_consumer=build_session_consumer(url=settings.RABBITMQ_URL,
                                          exchange=settings.EVENTS_EXCHANGE,
                                          guard=session_guard),
    readiness_checks={
        "consul":   _consul_ready,
        "web":      _web_ready,
        "sessions": _sessions_ready,
    },
    extra_tags=["edge", "portal"],
)

# WebSocket bridge for live notifications (events-service /ws). Registered on
# the app after create_app so it sits alongside the gateway routes.
app.add_api_websocket_route("/events/ws", events_ws_proxy)

# Serve the SPA last so it is the catch-all for everything that isn't an API
# route, the WebSocket, or the portal's own health/metrics endpoints.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="spa")
else:
    log.warning("portal.web_missing", path=str(WEB_DIR))


if __name__ == "__main__":
    import uvicorn
    tls = settings.tls_enabled
    if not tls and (settings.SSL_CERTFILE or settings.SSL_KEYFILE):
        log.warning("portal.tls.certs_missing", cert=settings.SSL_CERTFILE,
                    key=settings.SSL_KEYFILE)
    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT,
                reload=settings.DEBUG,
                ssl_certfile=settings.SSL_CERTFILE if tls else None,
                ssl_keyfile=settings.SSL_KEYFILE if tls else None)
