"""Tests for bot.feeds.binance_ws — BinanceFeed state and handlers."""
from __future__ import annotations

import time

import pytest

from bot.core.types import FeedSnapshot
from bot.feeds.binance_ws import BinanceFeed, _empty_state, WINDOW_SECS


# ---------------------------------------------------------------------------
# Construction & empty state
# ---------------------------------------------------------------------------

class TestConstruction:

    def test_initial_state_per_asset(self):
        feed = BinanceFeed()
        for asset in ("BTC", "ETH", "SOL"):
            assert asset in feed._state
            assert feed._state[asset]["last_price"] == 0.0
            assert feed._state[asset]["connected"] is False

    def test_initial_snapshot_is_default(self):
        feed = BinanceFeed()
        snap = feed.get_snapshot("BTC")
        assert isinstance(snap, FeedSnapshot)
        assert snap.last_price == 0.0
        assert snap.connected is False

    def test_unknown_asset_returns_default(self):
        feed = BinanceFeed()
        snap = feed.get_snapshot("DOGE")
        assert snap == FeedSnapshot()


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------

class TestIsHealthy:

    def test_not_healthy_when_disconnected(self):
        feed = BinanceFeed()
        assert feed.is_healthy("BTC") is False

    def test_healthy_when_connected_and_recent(self):
        feed = BinanceFeed()
        feed._state["BTC"]["connected"] = True
        feed._state["BTC"]["last_update"] = time.time()
        assert feed.is_healthy("BTC") is True

    def test_unhealthy_when_stale(self):
        feed = BinanceFeed()
        feed._state["BTC"]["connected"] = True
        feed._state["BTC"]["last_update"] = time.time() - 60
        assert feed.is_healthy("BTC") is False

    def test_unhealthy_for_unknown_asset(self):
        feed = BinanceFeed()
        assert feed.is_healthy("DOGE") is False


# ---------------------------------------------------------------------------
# _handle_agg_trade
# ---------------------------------------------------------------------------

class TestHandleAggTrade:

    def test_updates_last_price(self):
        feed = BinanceFeed()
        feed._handle_agg_trade("BTC", {"p": "67500.0", "q": "0.1", "m": False})
        assert feed._state["BTC"]["last_price"] == 67_500.0

    def test_positive_cvd_for_buy_aggressor(self):
        feed = BinanceFeed()
        # m=False -> buyer is taker -> positive CVD
        feed._handle_agg_trade("BTC", {"p": "67500.0", "q": "1.0", "m": False})
        assert feed._state["BTC"]["cvd_2min"] > 0

    def test_negative_cvd_for_sell_aggressor(self):
        feed = BinanceFeed()
        # m=True -> seller is maker (buyer is maker) -> negative CVD
        feed._handle_agg_trade("BTC", {"p": "67500.0", "q": "1.0", "m": True})
        assert feed._state["BTC"]["cvd_2min"] < 0

    def test_vwap_change_computed(self):
        feed = BinanceFeed()
        feed._handle_agg_trade("BTC", {"p": "67000.0", "q": "0.1", "m": False})
        feed._handle_agg_trade("BTC", {"p": "67500.0", "q": "0.1", "m": False})
        # vwap_change = (67500 - 67000) / 67000
        expected = (67_500 - 67_000) / 67_000
        assert feed._state["BTC"]["vwap_change"] == pytest.approx(expected, rel=1e-4)


# ---------------------------------------------------------------------------
# _handle_mark_price
# ---------------------------------------------------------------------------

class TestHandleMarkPrice:

    def test_funding_rate_stored(self):
        feed = BinanceFeed()
        feed._handle_mark_price("BTC", {"r": "0.0001"})
        assert feed._state["BTC"]["funding_rate"] == pytest.approx(0.0001)

    def test_missing_funding_rate_defaults_zero(self):
        feed = BinanceFeed()
        feed._handle_mark_price("BTC", {})
        assert feed._state["BTC"]["funding_rate"] == 0.0


# ---------------------------------------------------------------------------
# _handle_book_ticker
# ---------------------------------------------------------------------------

class TestHandleBookTicker:

    def test_bid_ask_stored(self):
        feed = BinanceFeed()
        feed._handle_book_ticker("BTC", {"b": "67499", "a": "67501", "B": "10", "A": "10"})
        assert feed._state["BTC"]["bid"] == 67_499.0
        assert feed._state["BTC"]["ask"] == 67_501.0

    def test_imbalance_positive_for_more_bids(self):
        feed = BinanceFeed()
        feed._handle_book_ticker("BTC", {"b": "67499", "a": "67501", "B": "100", "A": "50"})
        # imbalance = (100 - 50) / 150 = 0.333...
        assert feed._state["BTC"]["book_imbalance"] == pytest.approx(1 / 3, rel=1e-3)

    def test_imbalance_zero_when_balanced(self):
        feed = BinanceFeed()
        feed._handle_book_ticker("BTC", {"b": "67499", "a": "67501", "B": "50", "A": "50"})
        assert feed._state["BTC"]["book_imbalance"] == 0.0


# ---------------------------------------------------------------------------
# _handle_force_order
# ---------------------------------------------------------------------------

class TestHandleForceOrder:

    def test_long_liquidation(self):
        feed = BinanceFeed()
        # SELL side = engine closing LONG -> long liquidation
        feed._handle_force_order("BTC", {"o": {"S": "SELL", "ap": "67000", "z": "1.0"}})
        assert feed._state["BTC"]["liq_long_2min"] == pytest.approx(67_000.0)
        assert feed._state["BTC"]["liq_short_2min"] == 0.0

    def test_short_liquidation(self):
        feed = BinanceFeed()
        # BUY side = engine closing SHORT -> short liquidation
        feed._handle_force_order("BTC", {"o": {"S": "BUY", "ap": "67000", "z": "0.5"}})
        assert feed._state["BTC"]["liq_short_2min"] == pytest.approx(33_500.0)
        assert feed._state["BTC"]["liq_long_2min"] == 0.0


# ---------------------------------------------------------------------------
# Snapshot roundtrip
# ---------------------------------------------------------------------------

class TestSnapshotRoundtrip:

    def test_snapshot_reflects_state(self):
        feed = BinanceFeed()
        feed._state["ETH"]["last_price"] = 3_500.0
        feed._state["ETH"]["funding_rate"] = 0.0002
        feed._state["ETH"]["connected"] = True
        feed._state["ETH"]["last_update"] = time.time()

        snap = feed.get_snapshot("ETH")
        assert snap.last_price == 3_500.0
        assert snap.funding_rate == 0.0002
        assert snap.connected is True

    def test_snapshot_is_frozen(self):
        feed = BinanceFeed()
        snap = feed.get_snapshot("BTC")
        with pytest.raises(AttributeError):
            snap.last_price = 99_999.0  # type: ignore[misc]
