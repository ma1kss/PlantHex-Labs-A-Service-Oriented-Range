from __future__ import annotations

from pydantic import BaseModel


class ServiceHealth(BaseModel):
    service:        str
    up:             bool
    instance:       str = ""
    request_rate:   float | None = None     # req/s over the last 5m
    error_rate:     float | None = None     # 5xx req/s over the last 5m
    p95_latency_s:  float | None = None     # 95th-pct request duration (seconds)
    last_scrape:    str = ""
    last_error:     str = ""


class MonitoringSummary(BaseModel):
    prometheus_reachable: bool
    grafana_url:          str
    prometheus_url:       str
    dashboard_url:        str
    services_up:          int
    services_total:       int
    services:             list[ServiceHealth]


class TargetOut(BaseModel):
    service:     str
    instance:    str
    health:      str
    last_scrape: str
    last_error:  str
    scrape_url:  str


class InfoOut(BaseModel):
    grafana_url:    str
    prometheus_url: str
    dashboard_url:  str


class QueryResult(BaseModel):
    query:  str
    result: list[dict]


# Observations (begun by the Orchestration launch workflow)

class ObservationRequest(BaseModel):
    session_id:   str = ""
    challenge_id: str = ""
    target:       str = ""           # e.g. host:port being observed
    user_id:      str | None = None  # whose instance (defaults to caller)


class ObservationOut(BaseModel):
    observation_id: str
    session_id:     str
    challenge_id:   str
    user_id:        str
    target:         str
    started_at:     str
    active:         bool = True
