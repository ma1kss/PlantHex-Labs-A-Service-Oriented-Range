from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Iterable

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from cyber_range_core import get_logger
from cyber_range_core.auth import UserContext

from security import decode_token_str


log = get_logger(__name__)

# Routing-key prefixes everyone is allowed to receive. Anything else is
# admin/instructor only.
PUBLIC_PREFIXES = ("announcement.",)
STAFF_ROLES     = ("admin", "instructor")


@dataclass(eq=False)
class Connection:
    ws:    WebSocket
    user:  UserContext
    # Snapshot of roles at connect time. WS connections are cheap to drop /
    # reopen, so we don't bother chasing role mutations live.
    roles: list[str] = field(default_factory=list)


class WSManager:
    """In-memory pool of live connections. One process per service, so a
    plain set is enough — when we scale beyond one replica we'll need a
    Redis pub/sub or similar."""

    def __init__(self) -> None:
        self._conns: set[Connection] = set()
        self._lock = asyncio.Lock()

    async def connect(self, conn: Connection) -> None:
        async with self._lock:
            self._conns.add(conn)
        log.info("events.ws.connect",
                 user_id=conn.user.id, username=conn.user.username,
                 connections=len(self._conns))

    async def disconnect(self, conn: Connection) -> None:
        async with self._lock:
            self._conns.discard(conn)
        log.info("events.ws.disconnect",
                 user_id=conn.user.id, connections=len(self._conns))

    async def broadcast(self, envelope: dict, *, only_roles: Iterable[str] | None = None) -> None:
        """Send `envelope` to every connection (or only those whose user
        carries one of `only_roles`)."""
        only_set = set(only_roles) if only_roles else None
        async with self._lock:
            targets = list(self._conns)

        dead: list[Connection] = []
        for c in targets:
            if only_set is not None and not (only_set & set(c.roles)):
                continue
            try:
                await c.ws.send_json(envelope)
            except Exception:
                dead.append(c)

        if dead:
            async with self._lock:
                for c in dead:
                    self._conns.discard(c)

    async def fan_out_event(self, *, routing_key: str, service: str,
                            timestamp: str, payload: dict) -> None:
        """Convenience wrapper used by the RabbitMQ consumer + the
        announcement routes."""
        envelope = {
            "type":        "event",
            "routing_key": routing_key,
            "service":     service,
            "timestamp":   timestamp,
            "payload":     payload,
        }
        public = any(routing_key.startswith(p) for p in PUBLIC_PREFIXES)
        if public:
            await self.broadcast(envelope)
        else:
            await self.broadcast(envelope, only_roles=STAFF_ROLES)


router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = Query("")) -> None:
    settings = websocket.app.state.settings
    manager: WSManager = websocket.app.state.ws_manager

    if not token:
        await websocket.close(code=1008)  # policy violation
        return

    try:
        user = decode_token_str(token, settings)
    except ValueError as exc:
        log.info("events.ws.auth_failed", error=str(exc))
        await websocket.close(code=1008)
        return

    await websocket.accept()
    conn = Connection(ws=websocket, user=user, roles=list(user.roles))
    await manager.connect(conn)

    # Send a hello envelope so the client can render "connected" state.
    try:
        await websocket.send_json({
            "type":     "hello",
            "user_id":  user.id,
            "username": user.username,
            "roles":    list(user.roles),
        })
    except Exception:
        await manager.disconnect(conn)
        return

    try:
        # We don't expect inbound traffic — just block until the peer hangs up.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(conn)
