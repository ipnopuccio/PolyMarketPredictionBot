"""WebSocket and SSE streaming endpoints for real-time dashboard.

WebSocket: ws://host:port/api/v1/stream
SSE:       GET /api/v1/stream (Accept: text/event-stream)

Client → Server messages:
    {"action": "subscribe", "channels": ["prices", "signals"]}
    {"action": "unsubscribe", "channels": ["prices"]}

Server → Client messages:
    {"channel": "prices", "data": {...}, "timestamp": 1711439445.0}
    {"type": "heartbeat", "timestamp": 1711439475.0}
    {"type": "snapshot", "data": [...]}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse

from bot.dashboard.auth import _get_valid_keys
from bot.dashboard.ws_broker import WSBroker, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT

logger = logging.getLogger(__name__)

router = APIRouter()


def create_ws_router(broker: WSBroker) -> APIRouter:
    """Create the WebSocket/SSE router bound to a broker instance."""

    ws_router = APIRouter()

    # ------------------------------------------------------------------
    # WebSocket endpoint
    # ------------------------------------------------------------------

    @ws_router.websocket("/api/v1/stream")
    async def websocket_stream(ws: WebSocket) -> None:
        # Validate API key from query param or header before accepting
        api_key = ws.query_params.get("api_key") or ws.headers.get("x-api-key")
        if not api_key or api_key not in _get_valid_keys():
            await ws.close(code=4003, reason="Invalid or missing API key")
            return

        await ws.accept()
        client_id = str(uuid.uuid4())[:8]
        client = broker.add_client(client_id)

        logger.info("[WS] Client %s connected", client_id)

        # Send initial snapshot
        snapshot = broker.get_snapshot()
        if snapshot:
            await ws.send_json({"type": "snapshot", "data": snapshot})

        try:
            # Run sender and receiver concurrently
            await asyncio.gather(
                _ws_sender(ws, broker, client_id),
                _ws_receiver(ws, broker, client_id),
            )
        except WebSocketDisconnect:
            logger.info("[WS] Client %s disconnected", client_id)
        except Exception as e:
            logger.warning("[WS] Client %s error: %s", client_id, e)
        finally:
            broker.remove_client(client_id)

    # ------------------------------------------------------------------
    # SSE fallback endpoint
    # ------------------------------------------------------------------

    @ws_router.get("/api/v1/stream")
    async def sse_stream(request: Request) -> StreamingResponse:
        """Server-Sent Events fallback for environments without WebSocket."""
        api_key = request.query_params.get("api_key") or request.headers.get("x-api-key")
        if not api_key or api_key not in _get_valid_keys():
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Invalid or missing API key"}, status_code=403)

        client_id = f"sse-{str(uuid.uuid4())[:8]}"
        broker.add_client(client_id)

        async def event_generator():
            try:
                # Send initial snapshot
                snapshot = broker.get_snapshot()
                if snapshot:
                    data = json.dumps({"type": "snapshot", "data": snapshot})
                    yield f"data: {data}\n\n"

                while True:
                    if await request.is_disconnected():
                        break

                    msg = await broker.get_message(client_id, timeout=1.0)
                    if msg is not None:
                        yield f"data: {json.dumps(msg)}\n\n"
                    else:
                        # Send heartbeat comment to keep connection alive
                        yield f": heartbeat {time.time()}\n\n"

            finally:
                broker.remove_client(client_id)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return ws_router


# ------------------------------------------------------------------
# WebSocket sender/receiver tasks
# ------------------------------------------------------------------

async def _ws_sender(
    ws: WebSocket, broker: WSBroker, client_id: str,
) -> None:
    """Send queued messages and heartbeats to the client."""
    last_heartbeat = time.time()

    while True:
        msg = await broker.get_message(client_id, timeout=1.0)

        if msg is not None:
            await ws.send_json(msg)

        # Heartbeat
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            await ws.send_json({"type": "heartbeat", "timestamp": now})
            last_heartbeat = now


async def _ws_receiver(
    ws: WebSocket, broker: WSBroker, client_id: str,
) -> None:
    """Handle incoming client messages (subscribe/unsubscribe)."""
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send_json({"type": "error", "message": "Invalid JSON"})
            continue

        action = msg.get("action")
        channels = msg.get("channels", [])

        if action == "subscribe":
            subscribed = broker.subscribe(client_id, channels)
            await ws.send_json({
                "type": "subscribed",
                "channels": subscribed,
            })
        elif action == "unsubscribe":
            removed = broker.unsubscribe(client_id, channels)
            await ws.send_json({
                "type": "unsubscribed",
                "channels": removed,
            })
        else:
            await ws.send_json({
                "type": "error",
                "message": f"Unknown action: {action}",
            })
