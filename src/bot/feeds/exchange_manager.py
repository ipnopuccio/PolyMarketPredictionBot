"""Multi-exchange feed manager.

Orchestrates multiple ExchangeAdapters:
  - Aggregates prices (median) across exchanges
  - Detects outliers (>3 sigma from median)
  - Falls back to secondary exchanges when primary is down
  - Exposes unified FeedSnapshot (primary data + cross-exchange validation)
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from typing import Any

from bot.core.types import FeedSnapshot
from bot.feeds.exchange_adapter import ExchangeAdapter, ExchangeHealth, NormalizedTick
from bot.monitoring.metrics import EXCHANGE_LATENCY, EXCHANGE_PRICE, EXCHANGE_UP

logger = logging.getLogger(__name__)

# Outlier detection: reject ticks >3 sigma from median
OUTLIER_SIGMA = 3.0
# Minimum exchanges for meaningful median
MIN_EXCHANGES_FOR_MEDIAN = 2


class ExchangeManager:
    """Orchestrates multiple exchange adapters for robust price data.

    Architecture:
      - One PRIMARY adapter (Binance) provides full indicator data
        (CVD, funding, liquidations, book imbalance)
      - Zero or more SECONDARY adapters provide price validation
      - Manager computes median prices and detects outliers
      - If primary is down, secondary prices feed into FeedSnapshot

    Usage:
        manager = ExchangeManager()
        manager.add_adapter(BinanceAdapter(feed))
        manager.add_adapter(CCXTAdapter("coinbase"))
        await manager.start_all()

        # Get enriched snapshot (primary data + cross-exchange validation)
        snapshot = manager.get_snapshot("BTC")

        # Get aggregated price from all exchanges
        price = manager.get_median_price("BTC")
    """

    def __init__(self) -> None:
        self._adapters: list[ExchangeAdapter] = []
        self._primary: ExchangeAdapter | None = None

    # ------------------------------------------------------------------
    # Adapter management
    # ------------------------------------------------------------------

    def add_adapter(self, adapter: ExchangeAdapter) -> None:
        """Register an exchange adapter."""
        self._adapters.append(adapter)
        if adapter.is_primary:
            if self._primary is not None:
                raise ValueError("Only one primary adapter allowed")
            self._primary = adapter
        logger.info(
            "[ExchangeManager] Added %s (primary=%s)",
            adapter.name, adapter.is_primary,
        )

    @property
    def adapters(self) -> list[ExchangeAdapter]:
        return list(self._adapters)

    @property
    def primary(self) -> ExchangeAdapter | None:
        return self._primary

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        """Start all registered adapters."""
        await asyncio.gather(*(a.start() for a in self._adapters))
        logger.info(
            "[ExchangeManager] Started %d adapters (%s)",
            len(self._adapters),
            ", ".join(a.name for a in self._adapters),
        )

    async def stop_all(self) -> None:
        """Stop all registered adapters."""
        await asyncio.gather(
            *(a.stop() for a in self._adapters),
            return_exceptions=True,
        )
        logger.info("[ExchangeManager] All adapters stopped")

    # ------------------------------------------------------------------
    # Price aggregation
    # ------------------------------------------------------------------

    def get_all_ticks(self, asset: str) -> list[NormalizedTick]:
        """Collect latest ticks from all adapters for an asset."""
        ticks = []
        for adapter in self._adapters:
            tick = adapter.get_tick(asset)
            if tick is not None and tick.price > 0:
                ticks.append(tick)
                EXCHANGE_PRICE.labels(exchange=adapter.name, asset=asset).set(tick.price)
        return ticks

    def get_median_price(self, asset: str) -> float | None:
        """Median price across all exchanges for an asset.

        Returns None if no data available.
        """
        ticks = self.get_all_ticks(asset)
        if not ticks:
            return None
        prices = [t.price for t in ticks]
        return statistics.median(prices)

    def get_prices_by_exchange(self, asset: str) -> dict[str, float]:
        """Price from each exchange for an asset."""
        return {
            t.exchange: t.price
            for t in self.get_all_ticks(asset)
        }

    def detect_outliers(
        self, asset: str, sigma: float = OUTLIER_SIGMA,
    ) -> list[NormalizedTick]:
        """Find ticks that deviate >sigma standard deviations from median.

        Returns list of outlier ticks. Empty list = all prices agree.
        Needs at least MIN_EXCHANGES_FOR_MEDIAN exchanges to compute.
        """
        ticks = self.get_all_ticks(asset)
        if len(ticks) < MIN_EXCHANGES_FOR_MEDIAN:
            return []

        prices = [t.price for t in ticks]
        med = statistics.median(prices)
        if med == 0:
            return []

        # Use MAD (median absolute deviation) for robust spread estimation
        deviations = [abs(p - med) for p in prices]
        mad = statistics.median(deviations)
        if mad == 0:
            return []

        # MAD to sigma conversion factor
        threshold = sigma * mad * 1.4826

        return [t for t, p in zip(ticks, prices) if abs(p - med) > threshold]

    # ------------------------------------------------------------------
    # Snapshot (unified)
    # ------------------------------------------------------------------

    def get_snapshot(self, asset: str) -> FeedSnapshot:
        """Get a FeedSnapshot, preferring primary adapter data.

        If the primary adapter is healthy, returns its full snapshot
        (with CVD, funding, liquidations). If primary is down, constructs
        a basic snapshot from secondary adapters (price/bid/ask only).
        """
        # Try primary first
        if self._primary is not None:
            full = self._primary.get_full_snapshot(asset)
            if full is not None:
                return FeedSnapshot(
                    last_price=full.get("last_price", 0.0),
                    price_2min_ago=full.get("price_2min_ago", 0.0),
                    vwap_change=full.get("vwap_change", 0.0),
                    cvd_2min=full.get("cvd_2min", 0.0),
                    funding_rate=full.get("funding_rate", 0.0),
                    liq_long_2min=full.get("liq_long_2min", 0.0),
                    liq_short_2min=full.get("liq_short_2min", 0.0),
                    bid=full.get("bid", 0.0),
                    ask=full.get("ask", 0.0),
                    book_imbalance=full.get("book_imbalance", 0.0),
                    open_interest=full.get("open_interest", 0.0),
                    long_short_ratio=full.get("long_short_ratio", 0.0),
                    connected=full.get("connected", False),
                    last_update=full.get("last_update", 0.0),
                )

        # Fallback: use best available secondary tick
        ticks = self.get_all_ticks(asset)
        if not ticks:
            return FeedSnapshot()

        # Use the most recent tick
        best = max(ticks, key=lambda t: t.timestamp)
        return FeedSnapshot(
            last_price=best.price,
            bid=best.bid,
            ask=best.ask,
            connected=True,
            last_update=best.timestamp,
        )

    def is_healthy(self, asset: str) -> bool:
        """True if at least one adapter has fresh data for the asset."""
        for adapter in self._adapters:
            tick = adapter.get_tick(asset)
            if tick is not None and tick.price > 0:
                health = adapter.get_health()
                if health.is_healthy:
                    return True
        return False

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    def get_all_health(self) -> list[ExchangeHealth]:
        """Health status from all adapters."""
        healths = []
        for a in self._adapters:
            h = a.get_health()
            healths.append(h)
            EXCHANGE_UP.labels(exchange=h.exchange).set(1 if h.is_healthy else 0)
            EXCHANGE_LATENCY.labels(exchange=h.exchange).observe(h.latency_ms)
        return healths

    @property
    def exchange_count(self) -> int:
        """Number of registered exchanges."""
        return len(self._adapters)

    @property
    def healthy_count(self) -> int:
        """Number of currently healthy exchanges."""
        return sum(1 for h in self.get_all_health() if h.is_healthy)

    def summary(self) -> dict[str, Any]:
        """Summary dict for dashboard / health endpoint."""
        return {
            "total_exchanges": self.exchange_count,
            "healthy_exchanges": self.healthy_count,
            "exchanges": [
                {
                    "name": h.exchange,
                    "connected": h.connected,
                    "latency_ms": round(h.latency_ms, 1),
                    "stale_seconds": round(h.stale_seconds, 1),
                    "error_count": h.error_count,
                }
                for h in self.get_all_health()
            ],
        }
