"""Tests for BollingerStrategy — Bollinger Band breakout.

BUY_YES when price > upper, BUY_NO when price < lower, SKIP otherwise.
Confidence = min(1.0, distance_pct * 2) where distance_pct = (price - mid) / band_width.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from bot.core.types import Signal
from bot.strategies.bollinger import BollingerStrategy


# ── evaluate(): SKIP when bb=None ──────────────────────────────────────


class TestBollingerSkipNoBands:
    """Without Bollinger band data, always SKIP."""

    def test_skip_when_bb_none(self, feed_snapshot, bollinger_config):
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", feed_snapshot, bb=None)
        assert result.signal == Signal.SKIP
        assert result.confidence == 0.0

    def test_skip_when_bb_not_passed(self, feed_snapshot, bollinger_config):
        """bb defaults to None in the signature."""
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", feed_snapshot)
        assert result.signal == Signal.SKIP


# ── evaluate(): BUY_YES when price > upper ─────────────────────────────


class TestBollingerBuyYes:
    """Price above upper band triggers BUY_YES."""

    def test_buy_yes_price_above_upper(self, feed_snapshot, bollinger_config):
        bb = {"upper": 67_400.0, "lower": 67_200.0, "mid": 67_300.0}
        # price = 67_500 > upper = 67_400
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", feed_snapshot, bb=bb)
        assert result.signal == Signal.BUY_YES
        assert result.strategy == "BOLLINGER"

    def test_buy_yes_confidence_formula(self, feed_snapshot, bollinger_config):
        """confidence = min(1.0, distance_pct * 2)."""
        bb = {"upper": 67_400.0, "lower": 67_200.0, "mid": 67_300.0}
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", feed_snapshot, bb=bb)
        # band_width = 67400 - 67200 = 200
        # distance_pct = (67500 - 67300) / 200 = 1.0
        # confidence = min(1.0, 1.0 * 2) = 1.0
        assert result.confidence == pytest.approx(1.0)

    def test_buy_yes_small_breakout(self, feed_snapshot, bollinger_config):
        """Tiny breakout above upper band gives moderate confidence."""
        bb = {"upper": 67_490.0, "lower": 67_100.0, "mid": 67_295.0}
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", feed_snapshot, bb=bb)
        assert result.signal == Signal.BUY_YES
        # band_width = 67490 - 67100 = 390
        # distance_pct = (67500 - 67295) / 390 = 205/390 ~ 0.5256
        # confidence = min(1.0, 0.5256 * 2) ~ 1.0513 -> capped 1.0
        assert result.confidence == pytest.approx(1.0, abs=0.1)

    def test_buy_yes_indicators_populated(self, feed_snapshot, bollinger_config):
        bb = {"upper": 67_400.0, "lower": 67_200.0, "mid": 67_300.0}
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", feed_snapshot, bb=bb)
        assert result.indicators["bb_upper"] == 67_400.0
        assert result.indicators["bb_lower"] == 67_200.0
        assert result.indicators["bb_mid"] == 67_300.0


# ── evaluate(): BUY_NO when price < lower ──────────────────────────────


class TestBollingerBuyNo:
    """Price below lower band triggers BUY_NO."""

    def test_buy_no_price_below_lower(self, feed_snapshot, bollinger_config):
        snap = replace(feed_snapshot, last_price=67_100.0)
        bb = {"upper": 67_400.0, "lower": 67_200.0, "mid": 67_300.0}
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", snap, bb=bb)
        assert result.signal == Signal.BUY_NO

    def test_buy_no_confidence_formula(self, feed_snapshot, bollinger_config):
        snap = replace(feed_snapshot, last_price=67_100.0)
        bb = {"upper": 67_400.0, "lower": 67_200.0, "mid": 67_300.0}
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", snap, bb=bb)
        # band_width = 200, distance_pct = (67300 - 67100) / 200 = 1.0
        # confidence = min(1.0, 1.0 * 2) = 1.0
        assert result.confidence == pytest.approx(1.0)


# ── evaluate(): SKIP when within bands ─────────────────────────────────


class TestBollingerSkipWithinBands:
    """Price between lower and upper -> SKIP."""

    def test_skip_price_within_bands(self, feed_snapshot, bollinger_config):
        snap = replace(feed_snapshot, last_price=67_350.0)
        bb = {"upper": 67_400.0, "lower": 67_200.0, "mid": 67_300.0}
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", snap, bb=bb)
        assert result.signal == Signal.SKIP
        assert result.confidence == 0.0

    def test_skip_price_exactly_at_upper(self, feed_snapshot, bollinger_config):
        """Price == upper is NOT > upper, so SKIP."""
        snap = replace(feed_snapshot, last_price=67_400.0)
        bb = {"upper": 67_400.0, "lower": 67_200.0, "mid": 67_300.0}
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", snap, bb=bb)
        assert result.signal == Signal.SKIP

    def test_skip_price_exactly_at_lower(self, feed_snapshot, bollinger_config):
        """Price == lower is NOT < lower, so SKIP."""
        snap = replace(feed_snapshot, last_price=67_200.0)
        bb = {"upper": 67_400.0, "lower": 67_200.0, "mid": 67_300.0}
        strat = BollingerStrategy(bollinger_config)
        result = strat.evaluate("BTC", snap, bb=bb)
        assert result.signal == Signal.SKIP
