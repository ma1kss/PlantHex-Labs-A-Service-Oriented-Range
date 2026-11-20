from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from cyber_range_core import UserContext, get_logger

import schemas
from security import require_auth_local, require_role


EDITOR_ROLES = ("admin", "instructor")

log    = get_logger(__name__)
router = APIRouter()


def _prom(request: Request):
    return request.app.state.prometheus


def _settings(request: Request):
    return request.app.state.settings


def _dashboard_url(settings) -> str:
    base = settings.GRAFANA_PUBLIC_URL.rstrip("/")
    return f"{base}/d/{settings.GRAFANA_DASHBOARD_UID}/sor-platform-overview?orgId=1&kiosk"


# PromQL helpers

def _result_to_map(result: list[dict], label: str = "service") -> dict[str, float]:
    """Collapse an instant-query result list into {label_value: float}."""
    out: dict[str, float] = {}
    for series in result:
        metric = series.get("metric", {})
        key = metric.get(label)
        if key is None:
            continue
        value = series.get("value")  # [<ts>, "<float-as-str>"]
        if not value or len(value) < 2:
            continue
        try:
            out[key] = float(value[1])
        except (TypeError, ValueError):
            continue
    return out


# Endpoints

@router.get("/info",
            response_model=schemas.InfoOut,
            summary="Grafana / Prometheus URLs for the UI (admin / instructor)")
async def info(request: Request, _: UserContext = Depends(require_role(*EDITOR_ROLES))):
    s = _settings(request)
    return schemas.InfoOut(
        grafana_url=s.GRAFANA_PUBLIC_URL,
        prometheus_url=s.PROMETHEUS_PUBLIC_URL,
        dashboard_url=_dashboard_url(s),
    )


@router.get("/targets",
            response_model=list[schemas.TargetOut],
            summary="Prometheus scrape targets + health (admin / instructor)")
async def targets(request: Request, _: UserContext = Depends(require_role(*EDITOR_ROLES))):
    rows = await _prom(request).targets()
    return [schemas.TargetOut(**r) for r in rows]


@router.get("/summary",
            response_model=schemas.MonitoringSummary,
            summary="Per-service health overview (admin / instructor)")
async def summary(request: Request, _: UserContext = Depends(require_role(*EDITOR_ROLES))):
    prom = _prom(request)
    s    = _settings(request)

    reachable = await prom.reachable()
    if not reachable:
        return schemas.MonitoringSummary(
            prometheus_reachable=False,
            grafana_url=s.GRAFANA_PUBLIC_URL,
            prometheus_url=s.PROMETHEUS_PUBLIC_URL,
            dashboard_url=_dashboard_url(s),
            services_up=0, services_total=0, services=[],
        )

    # One round-trip per metric; collapse each into {service: value}.
    up_map   = _result_to_map(await prom.instant_query('up{job="sor-services"}'))
    rate_map = _result_to_map(await prom.instant_query(
        'sum by (service) (rate(http_requests_total[5m]))'))
    err_map  = _result_to_map(await prom.instant_query(
        'sum by (service) (rate(http_requests_total{status=~"5.."}[5m]))'))
    p95_map  = _result_to_map(await prom.instant_query(
        'histogram_quantile(0.95, sum by (service, le) '
        '(rate(http_request_duration_seconds_bucket[5m])))'))

    # Targets give us instance + last-scrape detail to merge in.
    tgt_by_service = {t["service"]: t for t in await prom.targets()}

    # The service set is exactly what `up{job="sor-services"}` returns — this
    # excludes Prometheus's own self-scrape target (job="prometheus"), which
    # isn't a SOR service.
    services_set = set(up_map)
    services: list[schemas.ServiceHealth] = []
    for name in sorted(services_set):
        tgt = tgt_by_service.get(name, {})
        services.append(schemas.ServiceHealth(
            service=name,
            up=up_map.get(name, 0.0) >= 1.0,
            instance=tgt.get("instance", ""),
            request_rate=rate_map.get(name),
            error_rate=err_map.get(name),
            p95_latency_s=p95_map.get(name),
            last_scrape=tgt.get("last_scrape", ""),
            last_error=tgt.get("last_error", ""),
        ))

    services_up = sum(1 for x in services if x.up)
    return schemas.MonitoringSummary(
        prometheus_reachable=True,
        grafana_url=s.GRAFANA_PUBLIC_URL,
        prometheus_url=s.PROMETHEUS_PUBLIC_URL,
        dashboard_url=_dashboard_url(s),
        services_up=services_up,
        services_total=len(services),
        services=services,
    )


@router.get("/query",
            response_model=schemas.QueryResult,
            summary="Proxy a PromQL instant query (admin only)")
async def query(request: Request,
                promql: Annotated[str, Query(min_length=1, max_length=2000)],
                _: UserContext = Depends(require_role("admin"))):
    result = await _prom(request).instant_query(promql)
    log.info("monitoring.query", promql=promql, series=len(result))
    return schemas.QueryResult(query=promql, result=result)


# Observations (begun/stopped by the Orchestration launch workflow)
# Monitoring is stateless (it reads Prometheus); observations are ephemeral —
# they track which live instances we're actively watching, kept in-memory and
# advertised on the bus so Data & Logging records the watch window.

def _obs(request: Request) -> dict:
    store = getattr(request.app.state, "observations", None)
    if store is None:
        store = request.app.state.observations = {}
    return store


@router.post("/observations",
             response_model=schemas.ObservationOut,
             status_code=status.HTTP_201_CREATED,
             summary="Begin observing a launched instance")
async def begin_observation(body: schemas.ObservationRequest,
                            request: Request,
                            caller: UserContext = Depends(require_auth_local)):
    target = body.user_id or caller.id
    if body.user_id and body.user_id != caller.id and not any(r in caller.roles for r in EDITOR_ROLES):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Observing another user's instance requires staff role")
    obs_id = str(uuid.uuid4())
    rec = {
        "observation_id": obs_id,
        "session_id":     body.session_id,
        "challenge_id":   body.challenge_id,
        "user_id":        target,
        "target":         body.target,
        "started_at":     datetime.now(timezone.utc).isoformat(),
        "active":         True,
    }
    _obs(request)[obs_id] = rec

    await request.app.state.publisher.publish(
        "monitoring.observation.started",
        {"observation_id": obs_id, "session_id": body.session_id,
         "challenge_id": body.challenge_id, "user_id": target},
        correlation_id=request.state.correlation_id,
    )
    log.info("monitoring.observation.started", observation_id=obs_id,
             session_id=body.session_id, user_id=target)
    return schemas.ObservationOut(**rec)


@router.delete("/observations/{observation_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Stop observing (owner or staff)")
async def stop_observation(observation_id: str,
                           request: Request,
                           caller: UserContext = Depends(require_auth_local)):
    store = _obs(request)
    rec = store.get(observation_id)
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Observation not found")
    if rec["user_id"] != caller.id and not any(r in caller.roles for r in EDITOR_ROLES):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your observation")
    store.pop(observation_id, None)

    await request.app.state.publisher.publish(
        "monitoring.observation.stopped",
        {"observation_id": observation_id, "session_id": rec.get("session_id"),
         "user_id": rec.get("user_id")},
        correlation_id=request.state.correlation_id,
    )
    log.info("monitoring.observation.stopped", observation_id=observation_id,
             user_id=rec.get("user_id"))
    return None


@router.get("/observations",
            response_model=list[schemas.ObservationOut],
            summary="List active observations (admin / instructor)")
async def list_observations(request: Request,
                            _: UserContext = Depends(require_role(*EDITOR_ROLES))):
    return [schemas.ObservationOut(**rec) for rec in _obs(request).values()]


@router.get("/alerts",
            summary="Firing performance/availability alerts, recent transitions and security alerts")
async def alerts(request: Request, _: UserContext = Depends(require_role(*EDITOR_ROLES))):
    w = request.app.state.alerts
    firing = [{"alertname": a.get("labels", {}).get("alertname", ""),
               "severity": a.get("labels", {}).get("severity", ""),
               "category": a.get("labels", {}).get("category", ""),
               "service": a.get("labels", {}).get("service", ""),
               "summary": a.get("annotations", {}).get("summary", ""),
               "active_since": a.get("activeAt", "")} for a in w.firing.values()]
    return {"firing": firing, "recent_transitions": list(w.history)[:50],
            "security_alerts": list(w.security)[:50]}

