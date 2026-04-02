"""Binance adapter — wraps the existing BinanceFeed as an ExchangeAdapter.

This is the PRIMARY adapter: it provides full indicator data (CVD, funding,
liquidations, book imbalance) in addition to normalized price ticks.
"""
from __future__ import annotations

import time
from typing import Any

from bot.feeds.binance_ws import BinanceFeed
from bot.feeds.exchange_adapter import ExchangeAdapter, ExchangeHealth, NormalizedTick


class BinanceAdapter(ExchangeAdapter):
    """Primary exchange adapter backed by the existing BinanceFeed."""

    def __init__(self, feed: BinanceFeed | None = None) -> None:
        self._feed = feed or BinanceFeed()
        self._health = ExchangeHealth(exchange="binance")
        self._running = False

    @property
    def name(self) -> str:
        return "binance"

    @property
    def is_primary(self) -> bool:
        return True

    @property
    def feed(self) -> BinanceFeed:
        """Direct access to the underlying BinanceFeed (for backward compat)."""
        return self._feed

    async def start(self) -> None:
        self._running = True
        self._health.connected = True
        self._health.last_update = time.time()
        # BinanceFeed.run() is a blocking coroutine — caller should
        # create_task(adapter.feed.run()) separately.

    async def stop(self) -> None:
        self._running = False
        self._health.connected = False

    def get_tick(self, asset: str) -> NormalizedTick | None:
        snapshot = self._feed.get_snapshot(asset)
        if snapshot.last_price <= 0:
            return None

        self._health.connected = snapshot.connected
        self._health.last_update = snapshot.last_update

        return NormalizedTick(
            exchange="binance",
            asset=asset,
            price=snapshot.last_price,
            volume=0.0,  # aggregate volume not tracked per-tick
            bid=snapshot.bid,
            ask=snapshot.ask,
            timestamp=snapshot.last_update,
        )

    def get_health(self) -> ExchangeHealth:
        if not self._running:
            self._health.connected = False
            return self._health
        # Sync health from any asset (use first healthy)
        for asset in ("BTC", "ETH", "SOL"):
            if self._feed.is_healthy(asset):
                self._health.connected = True
                snap = self._feed.get_snapshot(asset)
                self._health.last_update = snap.last_update
                self._health.latency_ms = (time.time() - snap.last_update) * 1000
                return self._health
        self._health.connected = False
        return self._health

    def get_full_snapshot(self, asset: str) -> dict[str, Any] | None:
        """Return full indicator data as dict (CVD, funding, liquidations, etc.)."""
        snapshot = self._feed.get_snapshot(asset)
        if not snapshot.connected:
            return None
        return snapshot.to_dict() | {
            "connected": snapshot.connected,
            "last_update": snapshot.last_update,
        }
