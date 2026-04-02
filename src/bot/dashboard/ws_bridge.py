"""Bridge between EventBus and WSBroker.

Subscribes to EventBus events and publishes them to the appropriate
WebSocket broker channels in real-time.

Events mapped:
    signal.evaluated  → channel: signals
    trade.placed      → channel: trades
    trade.resolved    → channel: trades
    price.updated     → channel: prices
    metrics.updated   → channel: metrics
"""
from __future__ import annotations

import logging
from typing import Any

from bot.core.events import EventBus
from bot.dashboard.ws_broker import WSBroker

logger = logging.getLogger(__name__)


class WSBridge:
    """Connects EventBus events to WSBroker channels."""

    def __init__(self, bus: EventBus, broker: WSBroker) -> None:
        self._bus = bus
        self._broker = broker

    def install(self) -> None:
        """Subscribe to all relevant EventBus events."""
        self._bus.subscribe("signal.evaluated", self._on_signal)
        self._bus.subscribe("trade.placed", self._on_trade_placed)
        self._bus.subscribe("trade.resolved", self._on_trade_resolved)
        self._bus.subscribe("price.updated", self._on_price_updated)
        self._bus.subscribe("metrics.updated", self._on_metrics_updated)
        logger.info("[WSBridge] Installed — 5 event handlers registered")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _on_signal(self, data: Any) -> None:
        """Strategy signal evaluation → signals channel."""
        await self._broker.publish("signals", data)

    async def _on_trade_placed(self, data: Any) -> None:
        """New trade placed → trades channel."""
        payload = data if isinstance(data, dict) else {"trade_id": data}
        payload["event"] = "placed"
        await self._broker.publish("trades", payload)

    async def _on_trade_resolved(self, data: Any) -> None:
        """Trade resolved → trades channel."""
        payload = data if isinstance(data, dict) else {"trade_id": data}
        payload["event"] = "resolved"
        await self._broker.publish("trades", payload)

    async def _on_price_updated(self, data: Any) -> None:
        """Price tick → prices channel."""
        await self._broker.publish("prices", data)

    async def _on_metrics_updated(self, data: Any) -> None:
        """Aggregated metrics → metrics channel."""
        await self._broker.publish("metrics", data)
