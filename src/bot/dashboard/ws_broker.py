"""In-memory WebSocket pub/sub broker.

Manages client connections, channel subscriptions, and message delivery.
Buffers messages during client disconnect (up to MAX_QUEUE per client).

Channels:
  - prices   : Real-time price ticks from all exchanges
  - signals  : Strategy signal evaluations with confidence
  - metrics  : Aggregated PnL, win rate, bankroll
  - trades   : Trade entries, exits, resolutions with PnL
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from bot.monitoring.metrics import WS_CLIENTS_CONNECTED, WS_MESSAGES_DROPPED, WS_MESSAGES_SENT

logger = logging.getLogger(__name__)

CHANNELS = frozenset({"prices", "signals", "metrics", "trades"})
MAX_QUEUE = 1000
HEARTBEAT_INTERVAL = 30  # seconds
HEARTBEAT_TIMEOUT = 60   # seconds — disconnect if no pong


@dataclass
class ClientState:
    """Tracks a single WebSocket client's subscription and queue state."""
    client_id: str
    channels: set[str] = field(default_factory=lambda: set(CHANNELS))
    queue: deque[dict] = field(default_factory=lambda: deque(maxlen=MAX_QUEUE))
    connected_at: float = field(default_factory=time.time)
    last_pong: float = field(default_factory=time.time)


class WSBroker:
    """In-memory pub/sub broker for WebSocket real-time streaming.

    Usage:
        broker = WSBroker()

        # Register a client
        client = broker.add_client("client_123")

        # Subscribe to specific channels
        broker.subscribe(client.client_id, ["prices", "signals"])

        # Publish a message (delivered to all subscribed clients)
        await broker.publish("prices", {"symbol": "BTC/USD", "price": 67500})

        # Get pending messages for a client
        messages = broker.drain(client.client_id)

        # Remove client on disconnect
        broker.remove_client("client_123")
    """

    def __init__(self) -> None:
        self._clients: dict[str, ClientState] = {}
        self._queues: dict[str, asyncio.Queue[dict]] = {}
        self._snapshot: dict[str, dict[str, Any]] = {ch: {} for ch in CHANNELS}

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    def add_client(self, client_id: str) -> ClientState:
        """Register a new client. Returns its state."""
        state = ClientState(client_id=client_id)
        self._clients[client_id] = state
        self._queues[client_id] = asyncio.Queue(maxsize=MAX_QUEUE)
        WS_CLIENTS_CONNECTED.set(len(self._clients))
        logger.info("[WSBroker] Client connected: %s", client_id)
        return state

    def remove_client(self, client_id: str) -> None:
        """Remove a client and clean up its queue."""
        self._clients.pop(client_id, None)
        self._queues.pop(client_id, None)
        WS_CLIENTS_CONNECTED.set(len(self._clients))
        logger.info("[WSBroker] Client disconnected: %s", client_id)

    def get_client(self, client_id: str) -> ClientState | None:
        return self._clients.get(client_id)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, client_id: str, channels: list[str]) -> list[str]:
        """Subscribe a client to channels. Returns actually subscribed list."""
        state = self._clients.get(client_id)
        if not state:
            return []
        valid = [ch for ch in channels if ch in CHANNELS]
        state.channels.update(valid)
        return valid

    def unsubscribe(self, client_id: str, channels: list[str]) -> list[str]:
        """Unsubscribe a client from channels. Returns actually removed list."""
        state = self._clients.get(client_id)
        if not state:
            return []
        valid = [ch for ch in channels if ch in CHANNELS]
        state.channels -= set(valid)
        return valid

    def get_subscriptions(self, client_id: str) -> set[str]:
        """Get current channel subscriptions for a client."""
        state = self._clients.get(client_id)
        return set(state.channels) if state else set()

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, channel: str, data: Any) -> int:
        """Publish a message to a channel. Returns number of clients reached.

        Messages are queued per-client for async delivery.
        """
        if channel not in CHANNELS:
            return 0

        msg = {
            "channel": channel,
            "data": data,
            "timestamp": time.time(),
        }

        # Update latest snapshot for this channel
        self._snapshot[channel] = msg
        WS_MESSAGES_SENT.labels(channel=channel).inc()

        delivered = 0
        for client_id, state in self._clients.items():
            if channel in state.channels:
                queue = self._queues.get(client_id)
                if queue is not None:
                    try:
                        queue.put_nowait(msg)
                        delivered += 1
                    except asyncio.QueueFull:
                        WS_MESSAGES_DROPPED.labels(channel=channel).inc()
                        # Drop oldest and add new
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            queue.put_nowait(msg)
                            delivered += 1
                        except asyncio.QueueFull:
                            pass

        return delivered

    # ------------------------------------------------------------------
    # Drain (get pending messages)
    # ------------------------------------------------------------------

    async def get_message(self, client_id: str, timeout: float = 1.0) -> dict | None:
        """Wait for the next message for a client (with timeout).

        Returns None on timeout (useful for heartbeat checks).
        """
        queue = self._queues.get(client_id)
        if queue is None:
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def drain(self, client_id: str) -> list[dict]:
        """Drain all pending messages for a client (non-blocking)."""
        queue = self._queues.get(client_id)
        if queue is None:
            return []
        messages = []
        while not queue.empty():
            try:
                messages.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    # ------------------------------------------------------------------
    # State snapshot (for reconnecting clients)
    # ------------------------------------------------------------------

    def get_snapshot(self) -> list[dict]:
        """Return the latest message from each channel (for reconnect sync)."""
        return [msg for msg in self._snapshot.values() if msg]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Broker status for health/debug endpoints."""
        return {
            "clients": self.client_count,
            "channels": list(CHANNELS),
            "queue_sizes": {
                cid: q.qsize()
                for cid, q in self._queues.items()
            },
        }
