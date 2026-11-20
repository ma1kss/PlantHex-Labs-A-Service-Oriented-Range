from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone

import httpx

from cyber_range_core import EventConsumer, get_logger

log = get_logger(__name__)


class AlertWatcher:
    def __init__(self, prometheus_url: str, publisher, poll: float = 10.0,
                 snapshot: float = 60.0):
        self.url = prometheus_url.rstrip("/")
        self.publisher = publisher
        self.poll, self.snapshot_every = poll, snapshot
        self.firing: dict[str, dict] = {}            # key -> alert
        self.history: deque = deque(maxlen=200)       # fired/resolved transitions
        self.security: deque = deque(maxlen=200)      # security alerts from the bus
        self._task: asyncio.Task | None = None

    @staticmethod
    def _key(a: dict) -> str:
        lab = a.get("labels", {})
        return "|".join(f"{k}={lab[k]}" for k in sorted(lab))

    async def _prom(self, path: str, **params) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(self.url + path, params=params)
            r.raise_for_status()
            return r.json().get("data", {})

    async def check_once(self) -> None:
        data = await self._prom("/api/v1/alerts")
        now_firing = {self._key(a): a for a in data.get("alerts", []) if a.get("state") == "firing"}
        for k, a in now_firing.items():
            if k not in self.firing:
                await self._emit("monitoring.alert.firing", a)
        for k, a in list(self.firing.items()):
            if k not in now_firing:
                await self._emit("monitoring.alert.resolved", a)
        self.firing = now_firing

    async def _emit(self, routing_key: str, a: dict) -> None:
        lab, ann = a.get("labels", {}), a.get("annotations", {})
        item = {"alertname": lab.get("alertname", ""), "severity": lab.get("severity", ""),
                "category": lab.get("category", ""), "target_service": lab.get("service", ""),
                "summary": ann.get("summary", ""), "active_since": a.get("activeAt", ""),
                "state": "firing" if routing_key.endswith("firing") else "resolved",
                "at": datetime.now(timezone.utc).isoformat()}
        self.history.appendleft(item)
        log.warning("monitoring.alert", **item)
        try:
            await self.publisher.publish(routing_key, {**item, "message": item["summary"]})
        except Exception as exc:
            log.warning("monitoring.alert.publish_failed", error=str(exc))

    async def snapshot_once(self) -> None:
        async def q(expr: str) -> dict[str, float]:
            out: dict[str, float] = {}
            for s in (await self._prom("/api/v1/query", query=expr)).get("result", []):
                svc = s.get("metric", {}).get("service")
                if svc:
                    try:
                        out[svc] = round(float(s["value"][1]), 6)
                    except (TypeError, ValueError):
                        pass
            return out
        up = await q('up{job="sor-services"}')
        rate = await q('sum by (service) (rate(http_requests_total[1m]))')
        err = await q('sum by (service) (rate(http_requests_total{status=~"5.."}[1m]))')
        p95 = await q('histogram_quantile(0.95, sum by (service, le) '
                      '(rate(http_request_duration_seconds_bucket[5m])))')
        services = {s: {"up": up.get(s), "req_per_s": rate.get(s), "err_per_s": err.get(s),
                        "p95_s": p95.get(s) if p95.get(s) == p95.get(s) else None}
                    for s in sorted(up)}
        await self.publisher.publish("monitoring.metrics.snapshot",
                                     {"kind": "telemetry", "services": services,
                                      "services_up": sum(1 for v in up.values() if v >= 1),
                                      "firing_alerts": len(self.firing)})

    async def _run(self) -> None:
        last_snap = 0.0
        loop = asyncio.get_running_loop()
        while True:
            try:
                await self.check_once()
                if loop.time() - last_snap >= self.snapshot_every:
                    await self.snapshot_once()
                    last_snap = loop.time()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("monitoring.alert.poll_failed", error=str(exc))
            await asyncio.sleep(self.poll)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()


def build_security_consumer(*, url: str, exchange: str, watcher: AlertWatcher) -> EventConsumer:
    consumer = EventConsumer(url=url, exchange_name=exchange,
                             queue_name="monitoring.security-alerts",
                             service_name="monitoring-service")

    @consumer.on("security.alert.#")
    async def _on_alert(routing_key: str, payload: dict) -> None:
        watcher.security.appendleft({
            "alertname": routing_key, "severity": payload.get("severity", "warning"),
            "category": "security", "raised_by": payload.get("service", ""),
            "summary": payload.get("message", routing_key),
            "at": payload.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "details": {k: v for k, v in payload.items()
                        if k not in ("event", "timestamp", "service", "correlation_id", "message")},
        })

    return consumer
