"""Tests for ExchangeManager (multi-exchange orchestrator)."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from bot.core.types import FeedSnapshot
from bot.feeds.exchange_adapter import ExchangeAdapter, ExchangeHealth, NormalizedTick
from bot.feeds.exchange_manager import ExchangeManager


# ---------------------------------------------------------------------------
# Test adapter stubs
# ---------------------------------------------------------------------------

class StubAdapter(ExchangeAdapter):
    """Minimal adapter for testing."""

    def __init__(
        self,
        name: str,
        primary: bool = False,
        ticks: dict[str, NormalizedTick] | None = None,
        healthy: bool = True,
        full_snapshot: dict | None = None,
    ):
        self._name = name
        self._primary = primary
        self._ticks = ticks or {}
        self._healthy = healthy
        self._full_snapshot = full_snapshot
        self._started = False
        self._stopped = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_primary(self) -> bool:
        return self._primary

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._stopped = True

    def get_tick(self, asset: str) -> NormalizedTick | None:
        return self._ticks.get(asset)

    def get_health(self) -> ExchangeHealth:
        return ExchangeHealth(
            exchange=self._name,
            connected=self._healthy,
            last_update=time.time() if self._healthy else 0,
        )

    def get_full_snapshot(self, asset: str) -> dict | None:
        return self._full_snapshot


def _tick(exchange: str, asset: str, price: float) -> NormalizedTick:
    return NormalizedTick(
        exchange=exchange, asset=asset, price=price, volume=1000,
        bid=price - 1, ask=price + 1, timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# Adapter management
# ---------------------------------------------------------------------------

class TestAdapterManagement:
    def test_add_adapter(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", primary=True))
        assert mgr.exchange_count == 1
        assert mgr.primary is not None

    def test_add_multiple_adapters(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", primary=True))
        mgr.add_adapter(StubAdapter("coinbase"))
        mgr.add_adapter(StubAdapter("kraken"))
        assert mgr.exchange_count == 3

    def test_only_one_primary_allowed(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", primary=True))
        with pytest.raises(ValueError, match="Only one primary"):
            mgr.add_adapter(StubAdapter("other", primary=True))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    async def test_start_all(self):
        a1 = StubAdapter("binance", primary=True)
        a2 = StubAdapter("coinbase")
        mgr = ExchangeManager()
        mgr.add_adapter(a1)
        mgr.add_adapter(a2)
        await mgr.start_all()
        assert a1._started and a2._started

    async def test_stop_all(self):
        a1 = StubAdapter("binance", primary=True)
        a2 = StubAdapter("coinbase")
        mgr = ExchangeManager()
        mgr.add_adapter(a1)
        mgr.add_adapter(a2)
        await mgr.stop_all()
        assert a1._stopped and a2._stopped


# ---------------------------------------------------------------------------
# Price aggregation
# ---------------------------------------------------------------------------

class TestPriceAggregation:
    def test_get_all_ticks(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", ticks={"BTC": _tick("binance", "BTC", 67500)}))
        mgr.add_adapter(StubAdapter("coinbase", ticks={"BTC": _tick("coinbase", "BTC", 67480)}))
        ticks = mgr.get_all_ticks("BTC")
        assert len(ticks) == 2

    def test_get_all_ticks_skips_zero_price(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", ticks={"BTC": _tick("binance", "BTC", 67500)}))
        mgr.add_adapter(StubAdapter("coinbase", ticks={"BTC": _tick("coinbase", "BTC", 0)}))
        ticks = mgr.get_all_ticks("BTC")
        assert len(ticks) == 1

    def test_median_price_single_exchange(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", ticks={"BTC": _tick("binance", "BTC", 67500)}))
        assert mgr.get_median_price("BTC") == 67500.0

    def test_median_price_multiple_exchanges(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("a", ticks={"BTC": _tick("a", "BTC", 67500)}))
        mgr.add_adapter(StubAdapter("b", ticks={"BTC": _tick("b", "BTC", 67480)}))
        mgr.add_adapter(StubAdapter("c", ticks={"BTC": _tick("c", "BTC", 67520)}))
        # Median of [67480, 67500, 67520] = 67500
        assert mgr.get_median_price("BTC") == 67500.0

    def test_median_price_none_when_no_data(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance"))
        assert mgr.get_median_price("BTC") is None

    def test_prices_by_exchange(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", ticks={"BTC": _tick("binance", "BTC", 67500)}))
        mgr.add_adapter(StubAdapter("coinbase", ticks={"BTC": _tick("coinbase", "BTC", 67480)}))
        prices = mgr.get_prices_by_exchange("BTC")
        assert prices == {"binance": 67500, "coinbase": 67480}


# ---------------------------------------------------------------------------
# Outlier detection
# ---------------------------------------------------------------------------

class TestOutlierDetection:
    def test_no_outliers_when_prices_agree(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("a", ticks={"BTC": _tick("a", "BTC", 67500)}))
        mgr.add_adapter(StubAdapter("b", ticks={"BTC": _tick("b", "BTC", 67502)}))
        mgr.add_adapter(StubAdapter("c", ticks={"BTC": _tick("c", "BTC", 67498)}))
        assert mgr.detect_outliers("BTC") == []

    def test_detects_outlier(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("a", ticks={"BTC": _tick("a", "BTC", 67500)}))
        mgr.add_adapter(StubAdapter("b", ticks={"BTC": _tick("b", "BTC", 67502)}))
        mgr.add_adapter(StubAdapter("c", ticks={"BTC": _tick("c", "BTC", 70000)}))  # way off
        outliers = mgr.detect_outliers("BTC")
        assert len(outliers) == 1
        assert outliers[0].exchange == "c"

    def test_needs_minimum_exchanges(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("a", ticks={"BTC": _tick("a", "BTC", 67500)}))
        # Only 1 exchange — can't compute outliers
        assert mgr.detect_outliers("BTC") == []


# ---------------------------------------------------------------------------
# Snapshot (unified)
# ---------------------------------------------------------------------------

class TestGetSnapshot:
    def test_primary_snapshot(self):
        full = {
            "last_price": 67500.0,
            "price_2min_ago": 67400.0,
            "vwap_change": 0.0005,
            "cvd_2min": 1_000_000.0,
            "funding_rate": 0.0001,
            "liq_long_2min": 50000,
            "liq_short_2min": 30000,
            "bid": 67499.0,
            "ask": 67501.0,
            "book_imbalance": 0.15,
            "open_interest": 5e9,
            "long_short_ratio": 1.05,
            "connected": True,
            "last_update": time.time(),
        }
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", primary=True, full_snapshot=full))
        snap = mgr.get_snapshot("BTC")
        assert isinstance(snap, FeedSnapshot)
        assert snap.last_price == 67500.0
        assert snap.cvd_2min == 1_000_000.0
        assert snap.connected is True

    def test_fallback_to_secondary(self):
        """When primary returns None, use secondary ticks."""
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", primary=True, full_snapshot=None))
        mgr.add_adapter(StubAdapter("coinbase", ticks={"BTC": _tick("coinbase", "BTC", 67400)}))
        snap = mgr.get_snapshot("BTC")
        assert snap.last_price == 67400.0
        assert snap.connected is True

    def test_empty_snapshot_when_no_data(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", primary=True, full_snapshot=None))
        snap = mgr.get_snapshot("BTC")
        assert snap.last_price == 0.0
        assert snap.connected is False

    def test_no_primary_uses_secondary(self):
        """Manager with only secondary adapters."""
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("coinbase", ticks={"BTC": _tick("coinbase", "BTC", 67300)}))
        snap = mgr.get_snapshot("BTC")
        assert snap.last_price == 67300.0


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_is_healthy_when_primary_up(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter(
            "binance", primary=True, healthy=True,
            ticks={"BTC": _tick("binance", "BTC", 67500)},
        ))
        assert mgr.is_healthy("BTC") is True

    def test_unhealthy_when_all_down(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", primary=True, healthy=False))
        assert mgr.is_healthy("BTC") is False

    def test_healthy_count(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", healthy=True))
        mgr.add_adapter(StubAdapter("coinbase", healthy=True))
        mgr.add_adapter(StubAdapter("kraken", healthy=False))
        assert mgr.healthy_count == 2

    def test_summary(self):
        mgr = ExchangeManager()
        mgr.add_adapter(StubAdapter("binance", primary=True, healthy=True))
        mgr.add_adapter(StubAdapter("coinbase", healthy=True))
        s = mgr.summary()
        assert s["total_exchanges"] == 2
        assert s["healthy_exchanges"] == 2
        assert len(s["exchanges"]) == 2
        assert s["exchanges"][0]["name"] == "binance"
