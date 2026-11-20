from __future__ import annotations

from datetime import datetime, timezone

from cyber_range_core import EventConsumer, get_logger


log = get_logger(__name__)


def build_consumer(*, url: str, exchange: str, queue: str,
                   ws_manager) -> EventConsumer:
    consumer = EventConsumer(
        url=url,
        exchange_name=exchange,
        queue_name=queue,
        service_name="events-service",
    )

    @consumer.on("#")
    async def _fanout(routing_key: str, payload: dict) -> None:
        service = str(payload.get("service") or "")
        ts_raw  = payload.get("timestamp")
        try:
            timestamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except Exception:
            timestamp = datetime.now(timezone.utc)

        try:
            await ws_manager.fan_out_event(
                routing_key=routing_key,
                service=service,
                timestamp=timestamp.isoformat(),
                payload=payload,
            )
        except Exception as exc:
            log.warning("events.fanout_failed",
                        routing_key=routing_key, error=str(exc))

    return consumer
