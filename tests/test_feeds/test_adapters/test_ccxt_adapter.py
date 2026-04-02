"""Tests for CCXTAdapter (secondary exchange adapter)."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.feeds.adapters.ccxt_adapter import CCXTAdapter, DEFAULT_SYMBOLS, EXCHANGE_SYMBOLS


class TestProperties:
    def test_name(self):
        adapter = CCXTAdapter("coinbase")
        assert adapter.name == "coinbase"

    def test_is_not_primary(self):
        adapter = CCXTAdapter("kraken")
        assert adapter.is_primary is False

    def test_no_full_snapshot(self):
        adapter = CCXTAdapter("coinbase")
        assert adapter.get_full_snapshot("BTC") is None


class TestSymbolMapping:
    def test_default_symbols(self):
        adapter = CCXTAdapter("bybit")
        # bybit not in EXCHANGE_SYMBOLS, uses defaults
        assert adapter._symbols["BTC"] == "BTC/USDT"
        assert adapter._symbols["ETH"] == "ETH/USDT"

    def test_exchange_specific_symbols(self):
        adapter = CCXTAdapter("coinbase")
        assert adapter._symbols["BTC"] == "BTC/USD"

    def test_custom_assets(self):
        adapter = CCXTAdapter("bybit", assets=("BTC",))
        assert "BTC" in adapter._symbols
        assert "ETH" not in adapter._symbols


class TestGetTick:
    def test_returns_none_before_start(self):
        adapter = CCXTAdapter("coinbase")
        assert adapter.get_tick("BTC") is None

    def test_returns_tick_after_fetch(self):
        adapter = CCXTAdapter("coinbase")
        # Simulate a fetched tick by setting internal state
        from bot.feeds.exchange_adapter import NormalizedTick
        adapter._ticks["BTC"] = NormalizedTick(
            exchange="coinbase", asset="BTC", price=67400, volume=1000,
            bid=67399, ask=67401, timestamp=time.time(),
        )
        tick = adapter.get_tick("BTC")
        assert tick is not None
        assert tick.price == 67400


class TestHealth:
    def test_initial_health(self):
        adapter = CCXTAdapter("coinbase")
        h = adapter.get_health()
        assert h.exchange == "coinbase"
        assert h.connected is False

    def test_health_after_errors(self):
        adapter = CCXTAdapter("coinbase")
        adapter._health.error_count = 5
        adapter._health.last_error = "timeout"
        h = adapter.get_health()
        assert h.error_count == 5
        assert h.last_error == "timeout"


class TestLifecycle:
    async def test_start_invalid_exchange_raises(self):
        adapter = CCXTAdapter("nonexistent_exchange_xyz")
        with pytest.raises(ValueError, match="Unknown exchange"):
            await adapter.start()

    async def test_stop_without_start(self):
        adapter = CCXTAdapter("coinbase")
        # Should not raise
        await adapter.stop()
        assert adapter.get_health().connected is False


class TestFetchAllTickers:
    async def test_fetch_updates_ticks(self):
        adapter = CCXTAdapter("coinbase", assets=("BTC",))

        # Mock the exchange
        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value={
            "last": 67500.0,
            "quoteVolume": 50_000_000,
            "bid": 67499.0,
            "ask": 67501.0,
        })
        adapter._exchange = mock_exchange

        await adapter._fetch_all_tickers()

        tick = adapter.get_tick("BTC")
        assert tick is not None
        assert tick.price == 67500.0
        assert tick.volume == 50_000_000
        assert tick.bid == 67499.0

    async def test_fetch_handles_errors_gracefully(self):
        adapter = CCXTAdapter("coinbase", assets=("BTC",))

        mock_exchange = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(side_effect=Exception("API down"))
        adapter._exchange = mock_exchange

        # Should not raise
        await adapter._fetch_all_tickers()
        # No tick stored
        assert adapter.get_tick("BTC") is None
