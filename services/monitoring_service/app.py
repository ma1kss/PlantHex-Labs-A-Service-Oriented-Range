from __future__ import annotations

from fastapi import FastAPI

from cyber_range_core import CoreSettings, create_app, get_logger

from prometheus_client_api import PrometheusClient
from routes import router
from alerting import AlertWatcher, build_security_consumer


class Settings(CoreSettings):
    SERVICE_NAME: str = "monitoring-service"
    PORT: int         = 8005

    # Token verification; the deployment sets RS256 and the IAM public key.
    JWT_SECRET:    str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER:    str = "cyber-range-iam"

    # Where Prometheus + Grafana live
    # In-cluster URL the service uses to query Prometheus.
    PROMETHEUS_URL:        str = "http://sor_prometheus:9090"
    # Browser-facing URLs handed to the SPA (published on the host).
    PROMETHEUS_PUBLIC_URL: str = "http://localhost:9090"
    GRAFANA_PUBLIC_URL:    str = "http://localhost:3000"
    GRAFANA_DASHBOARD_UID: str = "sor-overview"
    # Real-time alerting + metric snapshots (QoS-41, IFL-42).
    ALERT_POLL_SECONDS:      float = 10.0
    METRICS_SNAPSHOT_SECONDS: float = 60.0


settings = Settings()
log      = get_logger(__name__)
watcher: AlertWatcher | None = None


async def _on_startup(_app: FastAPI) -> None:
    if settings.JWT_SECRET == "change-me-in-prod":
        log.warning("monitoring.jwt.default_secret_in_use")
    _app.state.prometheus = PrometheusClient(settings.PROMETHEUS_URL)
    # Ephemeral registry of active observations (begun by Orchestration launches).
    _app.state.observations = {}
    _watcher.publisher = _app.state.publisher
    _app.state.alerts = _watcher
    _watcher.start()
    log.info("monitoring.ready", prometheus=settings.PROMETHEUS_URL,
             grafana=settings.GRAFANA_PUBLIC_URL)


async def _on_shutdown(_app: FastAPI) -> None:
    await _watcher.stop()


async def _consul_ready() -> bool:
    return isinstance(await app.state.registry.list_services(), dict)


async def _rabbit_ready() -> bool:
    publisher = app.state.publisher
    return await publisher.ensure_ready()


async def _prometheus_ready() -> bool:
    return await app.state.prometheus.reachable()


_watcher = AlertWatcher(settings.PROMETHEUS_URL, publisher=None,
                        poll=settings.ALERT_POLL_SECONDS,
                        snapshot=settings.METRICS_SNAPSHOT_SECONDS)

app = create_app(
    settings,
    routers=[router],
    on_startup=[_on_startup],
    on_shutdown=[_on_shutdown],
    event_consumer=build_security_consumer(url=settings.RABBITMQ_URL,
                                           exchange=settings.EVENTS_EXCHANGE,
                                           watcher=_watcher),
    readiness_checks={
        "consul":     _consul_ready,
        "rabbitmq":   _rabbit_ready,
        "prometheus": _prometheus_ready,
    },
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
