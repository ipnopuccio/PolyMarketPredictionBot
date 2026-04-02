"""Tests for bot.feeds.rsi_feed — RSI-14 and Bollinger Band calculator."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from bot.feeds.rsi_feed import RSIFeed, MAX_CANDLES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_candles(feed: RSIFeed, asset: str, prices: list[float]) -> None:
    """Feed one price per simulated minute to create closed candles.

    Each call to ``update`` happens in a distinct minute so that the
    previous candle closes and the price is committed to the closes deque.
    We need one extra ``update`` after the last price to close the final
    candle (the closing tick opens a new minute).
    """
    base_ts = 1_700_000_000  # arbitrary epoch
    for i, price in enumerate(prices):
        minute_ts = base_ts + i * 60
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = float(minute_ts)
            feed.update(asset, price)
    # One more tick in the next minute to close the last candle
    final_ts = base_ts + len(prices) * 60
    with patch("bot.feeds.rsi_feed.time") as mock_time:
        mock_time.time.return_value = float(final_ts)
        feed.update(asset, prices[-1])


# ---------------------------------------------------------------------------
# Basic update / candle formation
# ---------------------------------------------------------------------------

class TestUpdateAndCandles:

    def test_first_tick_does_not_create_candle(self):
        feed = RSIFeed()
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = 1_700_000_000.0
            feed.update("BTC", 67_000.0)
        assert feed.candle_counts["BTC"] == 0

    def test_minute_crossing_closes_candle(self):
        feed = RSIFeed()
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = 1_700_000_000.0
            feed.update("BTC", 67_000.0)
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = 1_700_000_060.0
            feed.update("BTC", 67_100.0)
        assert feed.candle_counts["BTC"] == 1

    def test_same_minute_no_new_candle(self):
        feed = RSIFeed()
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = 1_700_000_000.0
            feed.update("BTC", 67_000.0)
            feed.update("BTC", 67_050.0)
            feed.update("BTC", 67_100.0)
        assert feed.candle_counts["BTC"] == 0

    def test_candle_uses_last_price_as_close(self):
        """Closed candle should equal the last tick of that minute."""
        feed = RSIFeed()
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = 1_700_000_000.0
            feed.update("BTC", 67_000.0)
            feed.update("BTC", 67_999.0)  # last tick in this minute
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = 1_700_000_060.0
            feed.update("BTC", 68_000.0)
        assert list(feed._closes["BTC"])[-1] == 67_999.0

    def test_zero_price_ignored(self):
        feed = RSIFeed()
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = 1_700_000_000.0
            feed.update("BTC", 0.0)
        assert feed.candle_counts["BTC"] == 0

    def test_negative_price_ignored(self):
        feed = RSIFeed()
        with patch("bot.feeds.rsi_feed.time") as mock_time:
            mock_time.time.return_value = 1_700_000_000.0
            feed.update("BTC", -100.0)
        assert feed.candle_counts["BTC"] == 0


# ---------------------------------------------------------------------------
# RSI calculation
# ---------------------------------------------------------------------------

class TestRSI:

    def test_rsi_none_with_insufficient_candles(self):
        feed = RSIFeed()
        # Build 10 candles — RSI-14 needs 15 (period+1)
        _build_candles(feed, "BTC", [67_000 + i * 10 for i in range(10)])
        assert feed.get_rsi("BTC") is None

    def test_rsi_returned_with_sufficient_candles(self):
        feed = RSIFeed()
        # 16 prices -> 15 closed candles (period+1 for RSI-14)
        prices = [67_000 + i * 50 for i in range(16)]
        _build_candles(feed, "BTC", prices)
        rsi = feed.get_rsi("BTC")
        assert rsi is not None
        # All prices ascending -> RSI should be high (all gains, no losses)
        assert rsi == 100.0

    def test_rsi_all_down_returns_zero(self):
        feed = RSIFeed()
        prices = [70_000 - i * 50 for i in range(16)]
        _build_candles(feed, "BTC", prices)
        rsi = feed.get_rsi("BTC")
        assert rsi is not None
        assert rsi == pytest.approx(0.0)

    def test_rsi_mixed_movement(self):
        feed = RSIFeed()
        # Alternating up/down creates ~50 RSI
        prices = []
        base = 67_000
        for i in range(16):
            prices.append(base + (100 if i % 2 == 0 else -100))
        _build_candles(feed, "BTC", prices)
        rsi = feed.get_rsi("BTC")
        assert rsi is not None
        assert 20.0 < rsi < 80.0


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

class TestBollinger:

    def test_bollinger_none_with_insufficient_candles(self):
        feed = RSIFeed()
        # BB-20 needs 20 closed candles; build only 15
        _build_candles(feed, "BTC", [67_000 + i for i in range(15)])
        assert feed.get_bollinger("BTC") is None

    def test_bollinger_returned_with_sufficient_candles(self):
        feed = RSIFeed()
        # 21 prices -> 20 closed candles
        prices = [67_000 + i * 10 for i in range(21)]
        _build_candles(feed, "BTC", prices)
        bb = feed.get_bollinger("BTC")
        assert bb is not None
        assert "upper" in bb
        assert "mid" in bb
        assert "lower" in bb
        assert "pct" in bb
        assert bb["upper"] > bb["mid"] > bb["lower"]

    def test_bollinger_pct_in_range(self):
        feed = RSIFeed()
        prices = [67_000 + i * 10 for i in range(21)]
        _build_candles(feed, "BTC", prices)
        bb = feed.get_bollinger("BTC")
        assert bb is not None
        # Last price should be at or near the top of the bands (rising series)
        assert 0.0 <= bb["pct"] <= 1.5  # pct can exceed 1.0 outside bands

    def test_bollinger_constant_prices_narrow_bands(self):
        feed = RSIFeed()
        prices = [67_000.0] * 21
        _build_candles(feed, "BTC", prices)
        bb = feed.get_bollinger("BTC")
        assert bb is not None
        # Zero std -> upper == lower == mid
        assert bb["upper"] == bb["lower"] == bb["mid"]
        assert bb["pct"] == 0.5  # band_width == 0 fallback

    def test_max_candles_capped(self):
        feed = RSIFeed()
        # Feed more candles than MAX_CANDLES
        prices = [67_000 + i for i in range(MAX_CANDLES + 10)]
        _build_candles(feed, "BTC", prices)
        assert feed.candle_counts["BTC"] == MAX_CANDLES
