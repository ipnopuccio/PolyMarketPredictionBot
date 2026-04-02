"""Tests for TurboVwapStrategy — pure VWAP deviation.

Threshold = 0.0002 (default). Confidence = min(1.0, abs(vwap) / (threshold * 3)).
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from bot.core.types import Signal
from bot.strategies.turbo_vwap import TurboVwapStrategy


class TestTurboVwapBuyYes:
    """BUY_YES when vwap_change > threshold."""

    def test_buy_yes_above_threshold(self, feed_snapshot, turbo_vwap_config):
        """Default snapshot vwap=0.00074 >> 0.0002 threshold."""
        strat = TurboVwapStrategy(turbo_vwap_config)
        result = strat.evaluate("ETH", feed_snapshot)
        assert result.signal == Signal.BUY_YES
        assert result.strategy == "TURBO_VWAP"
        assert result.asset == "ETH"

    def test_buy_yes_confidence(self, feed_snapshot, turbo_vwap_config):
        snap = replace(feed_snapshot, vwap_change=0.0003)
        strat = TurboVwapStrategy(turbo_vwap_config)
        result = strat.evaluate("ETH", snap)
        # 0.0003 / (0.0002 * 3) = 0.0003 / 0.0006 = 0.5
        assert result.confidence == pytest.approx(0.5)

    def test_buy_yes_confidence_caps_at_1(self, feed_snapshot, turbo_vwap_config):
        snap = replace(feed_snapshot, vwap_change=0.001)
        strat = TurboVwapStrategy(turbo_vwap_config)
        result = strat.evaluate("ETH", snap)
        # 0.001 / 0.0006 = 1.667 -> capped to 1.0
        assert result.confidence == pytest.approx(1.0)


class TestTurboVwapBuyNo:
    """BUY_NO when vwap_change < -threshold."""

    def test_buy_no_below_neg_threshold(self, feed_snapshot, turbo_vwap_config):
        snap = replace(feed_snapshot, vwap_change=-0.0005)
        strat = TurboVwapStrategy(turbo_vwap_config)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.BUY_NO

    def test_buy_no_confidence(self, feed_snapshot, turbo_vwap_config):
        snap = replace(feed_snapshot, vwap_change=-0.0003)
        strat = TurboVwapStrategy(turbo_vwap_config)
        result = strat.evaluate("ETH", snap)
        # abs(-0.0003) / 0.0006 = 0.5
        assert result.confidence == pytest.approx(0.5)


class TestTurboVwapSkip:
    """SKIP when vwap_change within [-threshold, +threshold]."""

    def test_skip_vwap_below_threshold(self, feed_snapshot, turbo_vwap_config):
        snap = replace(feed_snapshot, vwap_change=0.0001)
        strat = TurboVwapStrategy(turbo_vwap_config)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.SKIP
        assert result.confidence == 0.0

    def test_skip_vwap_zero(self, feed_snapshot, turbo_vwap_config):
        snap = replace(feed_snapshot, vwap_change=0.0)
        strat = TurboVwapStrategy(turbo_vwap_config)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.SKIP
        assert result.confidence == 0.0
