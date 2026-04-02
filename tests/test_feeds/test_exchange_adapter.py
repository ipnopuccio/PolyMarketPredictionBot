"""Tests for ExchangeAdapter ABC and data types."""
from __future__ import annotations

import time

import pytest

from bot.feeds.exchange_adapter import ExchangeAdapter, ExchangeHealth, NormalizedTick


# ---------------------------------------------------------------------------
# NormalizedTick tests
# ---------------------------------------------------------------------------

class TestNormalizedTick:
    def test_spread(self):
        tick = NormalizedTick(
            exchange="test", asset="BTC", price=67000, volume=100,
            bid=66999, ask=67001,
        )
        assert tick.spread == pytest.approx(2.0)

    def test_spread_zero_when_no_quotes(self):
        tick = NormalizedTick(exchange="test", asset="BTC", price=67000, volume=100)
        assert tick.spread == 0.0

    def test_mid_price(self):
        tick = NormalizedTick(
            exchange="test", asset="BTC", price=67000, volume=100,
            bid=66990, ask=67010,
        )
        assert tick.mid == pytest.approx(67000.0)

    def test_mid_fallback_to_price(self):
        tick = NormalizedTick(exchange="test", asset="BTC", price=67000, volume=100)
        assert tick.mid == 67000.0

    def test_frozen(self):
        tick = NormalizedTick(exchange="test", asset="BTC", price=67000, volume=100)
        with pytest.raises(AttributeError):
            tick.price = 68000  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ExchangeHealth tests
# ---------------------------------------------------------------------------

class TestExchangeHealth:
    def test_healthy_when_connected_and_recent(self):
        h = ExchangeHealth(exchange="test", connected=True, last_update=time.time())
        assert h.is_healthy is True

    def test_unhealthy_when_disconnected(self):
        h = ExchangeHealth(exchange="test", connected=False, last_update=time.time())
        assert h.is_healthy is False

    def test_unhealthy_when_stale(self):
        h = ExchangeHealth(exchange="test", connected=True, last_update=time.time() - 60)
        assert h.is_healthy is False

    def test_stale_seconds(self):
        h = ExchangeHealth(exchange="test", connected=True, last_update=time.time() - 10)
        assert h.stale_seconds == pytest.approx(10, abs=1)

    def test_stale_seconds_no_update(self):
        h = ExchangeHealth(exchange="test")
        assert h.stale_seconds == float("inf")


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------

class TestABCCompliance:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            ExchangeAdapter()  # type: ignore[abstract]
