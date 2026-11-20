from __future__ import annotations

import asyncio

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from cyber_range_core import get_logger

from security import decode_token


log = get_logger(__name__)


async def events_ws_proxy(websocket: WebSocket) -> None:
    app = websocket.app
    s   = app.state.settings

    token = websocket.query_params.get("token", "")
    claims = decode_token(token, secret=s.JWT_SECRET,
                          algorithm=s.JWT_ALGORITHM, issuer=s.JWT_ISSUER)
    if claims is None or app.state.sessions.check(claims) is not None:
        await websocket.close(code=1008)   # policy violation
        return

    # Resolve the events-service and build the upstream ws URL.
    base = None
    try:
        endpoint = await app.state.registry.get_one("events-service")
        if endpoint is not None:
            base = endpoint.base_url
    except Exception:
        base = None
    if base is None:
        base = s.downstream_map().get("events-service")
    if base is None:
        await websocket.close(code=1011)   # internal error — no upstream
        return

    ws_url = base.replace("http://", "ws://").replace("https://", "wss://")
    ws_url = ws_url.rstrip("/") + f"/ws?token={token}"

    await websocket.accept()

    try:
        async with websockets.connect(ws_url, max_size=None) as upstream:
            await _pump_both(websocket, upstream)
    except Exception as exc:
        log.warning("portal.ws.upstream_error", error=str(exc))
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


async def _pump_both(browser: WebSocket, upstream) -> None:
    """Forward messages in both directions until either side closes."""

    async def browser_to_upstream():
        try:
            while True:
                msg = await browser.receive_text()
                await upstream.send(msg)
        except (WebSocketDisconnect, Exception):
            return

    async def upstream_to_browser():
        try:
            async for msg in upstream:
                if isinstance(msg, bytes):
                    await browser.send_bytes(msg)
                else:
                    await browser.send_text(msg)
        except Exception:
            return

    task_a = asyncio.create_task(browser_to_upstream())
    task_b = asyncio.create_task(upstream_to_browser())
    done, pending = await asyncio.wait(
        {task_a, task_b}, return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    try:
        await browser.close()
    except Exception:
        pass
