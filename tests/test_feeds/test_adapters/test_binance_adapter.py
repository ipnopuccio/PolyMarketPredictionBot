"""Tests for BinanceAdapter (primary adapter wrapping BinanceFeed)."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from bot.core.types import FeedSnapshot
from bot.feeds.adapters.binance import BinanceAdapter
from bot.feeds.exchange_adapter import NormalizedTick


def _mock_feed(
    price: float = 67500.0,
    connected: bool = True,
    healthy: bool = True,
) -> MagicMock:
    """Create a mock BinanceFeed."""
    feed = MagicMock()
    snapshot = FeedSnapshot(
        last_price=price,
        price_2min_ago=67400.0,
        vwap_change=0.0005,
        cvd_2min=1_000_000.0,
        funding_rate=0.0001,
        bid=price - 1,
        ask=price + 1,
        book_imbalance=0.15,
        open_interest=5e9,
        long_short_ratio=1.05,
        connected=connected,
        last_update=time.time(),
    )
    feed.get_snapshot.return_value = snapshot
    feed.is_healthy.return_value = healthy
    return feed


class TestBinanceAdapterProperties:
    def test_name(self):
        adapter = BinanceAdapter()
        assert adapter.name == "binance"

    def test_is_primary(self):
        adapter = BinanceAdapter()
        assert adapter.is_primary is True

    def test_feed_access(self):
        adapter = BinanceAdapter()
        assert adapter.feed is not None


class TestGetTick:
    def test_returns_normalized_tick(self):
        feed = _mock_feed(price=67500.0)
        adapter = BinanceAdapter(feed)
        tick = adapter.get_tick("BTC")

        assert tick is not None
        assert isinstance(tick, NormalizedTick)
        assert tick.exchange == "binance"
        assert tick.asset == "BTC"
        assert tick.price == 67500.0
        assert tick.bid == 67499.0
        assert tick.ask == 67501.0

    def test_returns_none_when_no_price(self):
        feed = _mock_feed(price=0.0)
        adapter = BinanceAdapter(feed)
        assert adapter.get_tick("BTC") is None


class TestGetFullSnapshot:
    def test_returns_dict_when_connected(self):
        feed = _mock_feed(connected=True)
        adapter = BinanceAdapter(feed)
        result = adapter.get_full_snapshot("BTC")

        assert result is not None
        assert result["last_price"] == 67500.0
        assert result["cvd_2min"] == 1_000_000.0
        assert result["connected"] is True

    def test_returns_none_when_disconnected(self):
        feed = _mock_feed(connected=False)
        adapter = BinanceAdapter(feed)
        assert adapter.get_full_snapshot("BTC") is None


class TestGetHealth:
    async def test_healthy_when_feed_healthy(self):
        feed = _mock_feed(healthy=True)
        adapter = BinanceAdapter(feed)
        await adapter.start()
        health = adapter.get_health()

        assert health.exchange == "binance"
        assert health.connected is True

    async def test_unhealthy_when_no_healthy_asset(self):
        feed = _mock_feed(healthy=False)
        adapter = BinanceAdapter(feed)
        await adapter.start()
        health = adapter.get_health()
        assert health.connected is False


class TestLifecycle:
    async def test_start_sets_connected(self):
        adapter = BinanceAdapter(_mock_feed())
        await adapter.start()
        assert adapter.get_health().connected is True

    async def test_stop_clears_connected(self):
        adapter = BinanceAdapter(_mock_feed())
        await adapter.start()
        await adapter.stop()
        assert adapter.get_health().connected is False
