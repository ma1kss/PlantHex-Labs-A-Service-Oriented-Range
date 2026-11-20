from __future__ import annotations

from datetime import datetime, timezone

from cyber_range_core import EventConsumer, get_logger


log = get_logger(__name__)


def _summarise(routing_key: str, payload: dict) -> str:
    """A short free-text line so full-text search has something to match."""
    bits = [routing_key]
    for k in ("username", "title", "challenge_id", "user_id", "name", "message"):
        v = payload.get(k)
        if v:
            bits.append(f"{k}={v}")
    return " ".join(str(b) for b in bits)


def build_consumer(*, url: str, exchange: str, queue: str, store) -> EventConsumer:
    consumer = EventConsumer(
        url=url,
        exchange_name=exchange,
        queue_name=queue,
        service_name="data-logging-service",
    )

    @consumer.on("#")
    async def _capture(routing_key: str, payload: dict) -> None:
        ts_raw = payload.get("timestamp")
        try:
            timestamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            ts_iso = timestamp.isoformat()
        except Exception:
            ts_iso = datetime.now(timezone.utc).isoformat()

        signer = str(payload.pop("_verified_signer", "") or "")
        doc = {
            "signer":         signer,
            "kind":           (payload.get("kind") if payload.get("kind") in ("log", "telemetry") else "event"),
            "routing_key":    routing_key,
            "service":        str(payload.get("service") or ""),
            "correlation_id": str(payload.get("correlation_id") or ""),
            "timestamp":      ts_iso,
            "message":        _summarise(routing_key, payload),
            "payload":        payload,
        }
        await store.index_record(doc)

    return consumer
