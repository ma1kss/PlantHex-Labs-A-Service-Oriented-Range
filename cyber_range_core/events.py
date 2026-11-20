from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import aio_pika

log = logging.getLogger(__name__)

# Type alias for consumer handlers
Handler = Callable[[str, dict[str, Any]], Awaitable[None]]


# Message-level security (SR-FR-IL-34, SEC-004, SEC-020)
#
# Every event is sealed by its publisher:
#   * the JSON envelope is encrypted with AES-256-GCM (key shared by the
#     platform services, the routing key is bound in as associated data), and
#   * the ciphertext is signed with the publishing service's own Ed25519 key.
# A consumer accepts a message only if the signature verifies against the
# public key of the service named in it (`kid`), the decrypted envelope names
# the same service, and decryption succeeds. In `enforce` mode unsigned or
# invalid messages are dropped and counted, so a message injected into the
# broker by anyone without a service key has no effect.
#
# Wire format (JSON body):
#   {"sor_sec": 1, "kid": "<service>", "n": b64(nonce), "ct": b64(ciphertext),
#    "sig": b64(Ed25519(b"sor-evt-v1|kid|routing_key|n|ct"))}

def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode())


class EventSecurityError(Exception):
    pass


class EventCrypto:
    def __init__(self, *, mode: str = "off", service_name: str = "",
                 signing_key_file: str = "", verify_keys_dir: str = "",
                 encryption_key_file: str = ""):
        self.mode = (mode or "off").lower()
        self.kid = service_name
        self._sk = None
        self._pubs: dict[str, Any] = {}
        self._aead = None
        if self.mode == "off":
            return
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        if signing_key_file and Path(signing_key_file).is_file():
            self._sk = serialization.load_pem_private_key(
                Path(signing_key_file).read_bytes(), password=None)
        if verify_keys_dir and Path(verify_keys_dir).is_dir():
            for f in Path(verify_keys_dir).glob("*.pub"):
                self._pubs[f.stem] = serialization.load_pem_public_key(f.read_bytes())
        if encryption_key_file and Path(encryption_key_file).is_file():
            self._aead = AESGCM(_b64d(Path(encryption_key_file).read_text().strip()))
        if self._aead is None or not self._pubs:
            raise RuntimeError("EVENT_SECURITY=enforce but event keys are missing")
        log.info("Event security enforced: kid=%s, %d verification keys, signing=%s",
                 self.kid, len(self._pubs), self._sk is not None)

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @staticmethod
    def _to_sign(kid: str, routing_key: str, n: str, ct: str) -> bytes:
        return f"sor-evt-v1|{kid}|{routing_key}|{n}|{ct}".encode()

    def seal(self, routing_key: str, envelope: dict) -> bytes:
        if not self.enabled:
            return json.dumps(envelope).encode()
        if self._sk is None:
            raise EventSecurityError(f"service {self.kid} has no signing key")
        nonce = os.urandom(12)
        ct = self._aead.encrypt(nonce, json.dumps(envelope).encode(), routing_key.encode())
        n, c = _b64e(nonce), _b64e(ct)
        sig = self._sk.sign(self._to_sign(self.kid, routing_key, n, c))
        return json.dumps({"sor_sec": 1, "kid": self.kid, "n": n, "ct": c,
                           "sig": _b64e(sig)}).encode()

    def open(self, routing_key: str, body: bytes) -> dict:
        obj = json.loads(body.decode())
        if not self.enabled:
            if isinstance(obj, dict) and obj.get("sor_sec"):
                raise EventSecurityError("sealed message but event security is off")
            return obj
        if not (isinstance(obj, dict) and obj.get("sor_sec") == 1):
            raise EventSecurityError("unsigned message")
        kid = str(obj.get("kid", ""))
        pub = self._pubs.get(kid)
        if pub is None:
            raise EventSecurityError(f"unknown signer '{kid}'")
        try:
            pub.verify(_b64d(obj["sig"]), self._to_sign(kid, routing_key, obj["n"], obj["ct"]))
        except Exception:
            raise EventSecurityError("bad signature")
        try:
            env = json.loads(self._aead.decrypt(_b64d(obj["n"]), _b64d(obj["ct"]),
                                                routing_key.encode()))
        except Exception:
            raise EventSecurityError("decryption failed")
        if env.get("service") != kid:
            raise EventSecurityError("envelope service does not match signer")
        env["_verified_signer"] = kid
        return env


_CRYPTO = EventCrypto()
_REJECTED = None


def configure_event_security(settings) -> EventCrypto:
    """Called by create_app(); every publisher/consumer in the process uses it."""
    global _CRYPTO
    _CRYPTO = EventCrypto(
        mode=getattr(settings, "EVENT_SECURITY", "off"),
        service_name=getattr(settings, "SERVICE_NAME", ""),
        signing_key_file=getattr(settings, "EVENT_SIGNING_KEY_FILE", ""),
        verify_keys_dir=getattr(settings, "EVENT_VERIFY_KEYS_DIR", ""),
        encryption_key_file=getattr(settings, "EVENT_ENCRYPTION_KEY_FILE", ""),
    )
    return _CRYPTO


def event_crypto() -> EventCrypto:
    return _CRYPTO


def _count_rejected(reason: str) -> None:
    global _REJECTED
    try:
        if _REJECTED is None:
            from prometheus_client import Counter
            _REJECTED = Counter("sor_events_rejected_total",
                                "Bus messages dropped by event security", ["reason"])
        _REJECTED.labels(reason=reason).inc()
    except Exception:
        pass


class EventPublisher:
    """
    Async, connection-pooled publisher.

    Usage:
        publisher = EventPublisher(settings)
        await publisher.start()
        await publisher.publish("exercise.instance.started",
                                {"instance_id": 42, "player_id": "alice"})
        await publisher.stop()

    create_app() handles start/stop for you.
    """

    def __init__(self, url: str, exchange_name: str, service_name: str = "unknown"):
        self.url           = url
        self.exchange_name = exchange_name
        self.service_name  = service_name
        self._conn: aio_pika.abc.AbstractRobustConnection | None  = None
        self._channel: aio_pika.abc.AbstractChannel | None        = None
        self._exchange: aio_pika.abc.AbstractExchange | None      = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def start(self) -> None:
        if not self.enabled:
            log.info("Event bus disabled (no RABBITMQ_URL) — events will be logged.")
            return
        await self._connect()

    async def ensure_ready(self) -> bool:
        """Readiness probe that self-heals: (re)connect lazily if the exchange
        isn't live yet, then report whether it is. A disabled bus counts as
        ready (events are logged locally by design). Because the /ready endpoint
        is polled by the container health-check, a service that lost the broker
        at startup (or a broker restart) reconnects on its own within one probe
        interval — no manual restart needed."""
        if not self.enabled:
            return True
        if self._exchange is None or (self._conn is not None and self._conn.is_closed):
            await self._connect()
        return self._exchange is not None

    async def stop(self) -> None:
        try:
            if self._conn and not self._conn.is_closed:
                await self._conn.close()
        except Exception:
            pass

    async def _connect(self) -> None:
        async with self._lock:
            if self._exchange is not None and self._conn and not self._conn.is_closed:
                return
            try:
                self._conn     = await aio_pika.connect_robust(self.url)
                self._channel  = await self._conn.channel(publisher_confirms=True)
                self._exchange = await self._channel.declare_exchange(
                    self.exchange_name, aio_pika.ExchangeType.TOPIC, durable=True,
                )
                log.info("Connected to RabbitMQ at %s (exchange=%s)",
                         self.url, self.exchange_name)
            except Exception as exc:
                log.warning("RabbitMQ connection failed (%s) — will log events locally.", exc)
                self._exchange = None

    async def publish(
        self,
        routing_key: str,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> None:
        """
        Fire-and-forget. If the broker is unavailable we log and move on.
        Every event carries: timestamp, service, correlation_id, routing_key.
        """
        # Identity fields are set last so a payload can never override them:
        # "service" is what consumers check against the message signer.
        envelope = {
            **payload,
            "event":          routing_key,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "service":        self.service_name,
            "correlation_id": correlation_id or str(uuid.uuid4()),
        }

        if not self.enabled:
            log.info("EVENT[local] %s %s", routing_key, envelope)
            return

        if self._exchange is None:
            await self._connect()

        if self._exchange is None:
            log.info("EVENT[fallback-local] %s %s", routing_key, envelope)
            return

        try:
            message = aio_pika.Message(
                body=_CRYPTO.seal(routing_key, envelope),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                correlation_id=envelope["correlation_id"],
                timestamp=datetime.now(timezone.utc),
            )
            await self._exchange.publish(message, routing_key=routing_key)
        except Exception as exc:
            log.error("EVENT publish failed (%s) — event=%s", exc, routing_key)


# Consumer base class

class EventConsumer:
    """
    Subclass and register handlers via .on(pattern):

        consumer = EventConsumer(settings, queue_name="learning-service")
        consumer.on("exercise.flag.submitted", handle_flag)
        consumer.on("exercise.instance.stopped", handle_stop)
        await consumer.start()

    Patterns use AMQP topic wildcards:
       *  — exactly one word
       #  — zero or more words

    Each handler gets (routing_key, payload_dict).
    """

    def __init__(
        self,
        url: str,
        exchange_name: str,
        queue_name: str,
        service_name: str = "unknown",
    ):
        self.url           = url
        self.exchange_name = exchange_name
        self.queue_name    = queue_name
        self.service_name  = service_name
        self._handlers: list[tuple[str, Handler]] = []
        self._conn: aio_pika.abc.AbstractRobustConnection | None = None
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def on(self, pattern: str, handler: Handler | None = None):
        """Register a handler. Can be used as decorator: @consumer.on('x.y.z')"""
        def _reg(h: Handler):
            self._handlers.append((pattern, h))
            return h
        return _reg(handler) if handler else _reg

    async def start(self) -> None:
        if not self.enabled or not self._handlers:
            log.info("EventConsumer disabled or no handlers registered — skipping.")
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        try:
            if self._conn and not self._conn.is_closed:
                await self._conn.close()
        except Exception:
            pass

    async def _run(self) -> None:
        # Supervision loop: reconnect with capped exponential backoff whenever
        # the connection can't be established (e.g. RabbitMQ not up yet at
        # startup) or drops mid-consume. Without this the consumer would exit
        # on the first error and never re-attach — leaving a durable queue with
        # zero consumers while messages pile up.
        backoff = 1.0
        while True:
            try:
                self._conn = await aio_pika.connect_robust(self.url)
                channel    = await self._conn.channel()
                await channel.set_qos(prefetch_count=32)
                exchange   = await channel.declare_exchange(
                    self.exchange_name, aio_pika.ExchangeType.TOPIC, durable=True,
                )
                queue = await channel.declare_queue(self.queue_name, durable=True)
                for pattern, _ in self._handlers:
                    await queue.bind(exchange, routing_key=pattern)
                    log.info("Consumer bound queue=%s pattern=%s", self.queue_name, pattern)

                backoff = 1.0   # connected cleanly — reset the backoff
                log.info("EventConsumer consuming queue=%s", self.queue_name)
                async with queue.iterator() as it:
                    async for msg in it:
                        async with msg.process(requeue=False):
                            await self._dispatch(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("EventConsumer error (%s) — reconnecting in %.0fs",
                          exc, backoff)
                try:
                    if self._conn and not self._conn.is_closed:
                        await self._conn.close()
                except Exception:
                    pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _dispatch(self, msg: aio_pika.abc.AbstractIncomingMessage) -> None:
        routing = msg.routing_key or ""
        try:
            payload = _CRYPTO.open(routing, msg.body)
        except EventSecurityError as exc:
            log.warning("Rejected event %s on queue %s: %s", routing, self.queue_name, exc)
            _count_rejected(str(exc).split(" '")[0])
            return
        except Exception as exc:
            log.warning("Could not decode event body: %s", exc)
            _count_rejected("undecodable")
            return

        for pattern, handler in self._handlers:
            if _topic_matches(pattern, routing):
                try:
                    await handler(routing, payload)
                except Exception as exc:
                    log.error("Handler for %s raised: %s", pattern, exc)


def _topic_matches(pattern: str, routing_key: str) -> bool:
    """Minimal AMQP topic matcher (*, #)."""
    p_parts = pattern.split(".")
    r_parts = routing_key.split(".")

    def match(i: int, j: int) -> bool:
        if i == len(p_parts):
            return j == len(r_parts)
        if p_parts[i] == "#":
            # match zero or more words
            if i == len(p_parts) - 1:
                return True
            for k in range(j, len(r_parts) + 1):
                if match(i + 1, k):
                    return True
            return False
        if j == len(r_parts):
            return False
        if p_parts[i] == "*" or p_parts[i] == r_parts[j]:
            return match(i + 1, j + 1)
        return False

    return match(0, 0)
