from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Awaitable

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from cyber_range_core.settings      import CoreSettings
from cyber_range_core.logging_setup import configure_logging, get_logger, set_correlation_id
from cyber_range_core.registry      import RegistryClient, make_service_id
from cyber_range_core.events        import EventPublisher, EventConsumer, configure_event_security
from cyber_range_core.health        import make_health_router
from cyber_range_core.auth          import _get_settings_placeholder


log = logging.getLogger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """
    Picks up or generates an X-Correlation-Id on each request and makes it
    available to log records via the contextvar set in logging_setup.
    """
    async def dispatch(self, request: Request, call_next):
        corr = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        set_correlation_id(corr)
        request.state.correlation_id = corr
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = corr
        return response


def create_app(
    settings: CoreSettings,
    *,
    routers: list[APIRouter] | None = None,
    on_startup: list[Callable[[FastAPI], Awaitable[None]]] | None = None,
    on_shutdown: list[Callable[[FastAPI], Awaitable[None]]] | None = None,
    event_consumer: EventConsumer | None = None,
    readiness_checks: dict[str, Callable[[], Awaitable[bool]]] | None = None,
    extra_tags: list[str] | None = None,
) -> FastAPI:
    """
    Build a fully-wired FastAPI app for a cyber-range service.
    Returns an app ready to be served by uvicorn.
    """
    configure_logging(settings.SERVICE_NAME, settings.LOG_LEVEL, settings.LOG_FORMAT)
    slog = get_logger(__name__)
    # Message-level security for every publisher/consumer in this process.
    configure_event_security(settings)

    # Singleton helpers attached to app.state
    registry = RegistryClient(
        host=settings.CONSUL_HOST,
        port=settings.CONSUL_PORT,
        scheme=settings.CONSUL_SCHEME,
    )
    publisher = EventPublisher(
        url=settings.RABBITMQ_URL,
        exchange_name=settings.EVENTS_EXCHANGE,
        service_name=settings.SERVICE_NAME,
    )
    service_id = make_service_id(settings.SERVICE_NAME, settings.PORT)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings   = settings
        app.state.registry   = registry
        app.state.publisher  = publisher
        app.state.service_id = service_id

        try:
            await _startup(app)
        except BaseException:
            # A failed startup (e.g. Postgres not yet up after a host reboot,
            # when Docker restarts containers without depends_on ordering)
            # must let the process exit so the restart policy retries it.
            # Without this, the consumer's reconnect loop keeps the event
            # loop alive and the container stays up but never serves.
            slog.error("service.startup.failed", service=settings.SERVICE_NAME)
            for closer in ((event_consumer.stop if event_consumer else None),
                           publisher.stop, registry.close):
                if closer is None:
                    continue
                try:
                    await closer()
                except Exception:
                    pass
            raise

        yield

        # Shutdown
        slog.info("service.stopping", service=settings.SERVICE_NAME)
        for hook in on_shutdown or []:
            try:
                await hook(app)
            except Exception as exc:
                slog.error("shutdown.hook.failed", error=str(exc))

        if event_consumer:
            try:
                await event_consumer.stop()
            except Exception:
                pass

        try:
            await publisher.stop()
        except Exception:
            pass
        try:
            await registry.deregister(service_id)
            await registry.close()
        except Exception:
            pass

        slog.info("service.stopped", service=settings.SERVICE_NAME)

    async def _startup(app: FastAPI) -> None:
        # Start event publisher (connects to Rabbit)
        await publisher.start()

        # Register with Consul (best-effort). The advertised scheme must match
        # what the service actually serves: a TLS edge registers an https
        # health check (self-signed, so Consul skips verification) and a
        # `scheme` meta entry that Prometheus relabels into __scheme__.
        scheme = "https" if settings.tls_enabled else "http"
        health_url = f"{scheme}://{settings.PUBLIC_HOSTNAME}:{settings.PORT}/health"
        registered = await registry.register(
            service_id=service_id,
            service_name=settings.SERVICE_NAME,
            address=settings.PUBLIC_HOSTNAME,
            port=settings.PORT,
            tags=["cyber-range", f"v{settings.SERVICE_VERSION}"] + (extra_tags or []),
            health_check_url=health_url,
            meta={"version": settings.SERVICE_VERSION, "scheme": scheme},
            health_tls_skip_verify=settings.tls_enabled,
        )
        # Sweep registrations left behind by previous containers of this
        # service (their IDs embed the old container hostname).
        if registered:
            await registry.deregister_stale(settings.SERVICE_NAME, keep_id=service_id)

        # Start event consumer (optional)
        if event_consumer:
            await event_consumer.start()
            app.state.event_consumer = event_consumer

        # User-supplied startup hooks
        for hook in on_startup or []:
            await hook(app)

        slog.info("service.started",
                  service=settings.SERVICE_NAME,
                  port=settings.PORT,
                  auth=settings.AUTH_ENABLED,
                  consul=bool(settings.CONSUL_HOST),
                  events=bool(settings.RABBITMQ_URL))

    # Build the app
    app = FastAPI(
        title=settings.SERVICE_NAME,
        version=settings.SERVICE_VERSION,
        description=f"PHL service: {settings.SERVICE_NAME}",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Correlation-Id"],
    )
    app.add_middleware(CorrelationIdMiddleware)

    # Human-readable request-validation errors: the SPA shows `detail`
    # verbatim, so return "Password must be at least 6 characters." instead
    # of Pydantic's raw error list.
    @app.exception_handler(RequestValidationError)
    async def _clean_validation_error(request: Request, exc: RequestValidationError):
        e = exc.errors()[0]
        loc = [str(p) for p in e.get("loc", []) if p not in ("body", "query", "path")]
        field = (loc[-1] if loc else "input").replace("_", " ").capitalize()
        ctx = e.get("ctx") or {}
        kind = e.get("type", "")
        if kind == "string_too_short":
            msg = f"{field} must be at least {ctx.get('min_length')} characters."
        elif kind == "string_too_long":
            msg = f"{field} must be at most {ctx.get('max_length')} characters."
        elif kind == "missing":
            msg = f"{field} is required."
        else:
            msg = f"{field}: {e.get('msg', 'invalid value')}"
        return JSONResponse({"detail": msg}, status_code=422)

    # Wire settings into the auth dependency
    app.dependency_overrides[_get_settings_placeholder] = lambda: settings

    # Health/ready endpoints
    app.include_router(
        make_health_router(settings.SERVICE_NAME, settings.SERVICE_VERSION, readiness_checks),
    )

    for r in routers or []:
        app.include_router(r)

    if settings.METRICS_ENABLED:
        try:
            from prometheus_fastapi_instrumentator import Instrumentator
            Instrumentator(
                should_group_status_codes=False,
                excluded_handlers=["/metrics", "/health", "/ready"],
            ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
        except Exception as exc:  # never let metrics wiring kill a service
            slog.warning("metrics.instrument_failed", error=str(exc))

    return app
