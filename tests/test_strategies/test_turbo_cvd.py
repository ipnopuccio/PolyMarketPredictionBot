"""Tests for TurboCvdStrategy — pure CVD pressure.

Threshold = 200_000 (default). Confidence = min(1.0, abs(cvd) / (threshold * 3)).
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from bot.core.types import Signal
from bot.strategies.turbo_cvd import TurboCvdStrategy


class TestTurboCvdBuyYes:
    """BUY_YES when cvd > threshold."""

    def test_buy_yes_above_threshold(self, feed_snapshot, turbo_cvd_config):
        """Default snapshot cvd=1.5M >> 200K threshold."""
        strat = TurboCvdStrategy(turbo_cvd_config)
        result = strat.evaluate("ETH", feed_snapshot)
        assert result.signal == Signal.BUY_YES
        assert result.strategy == "TURBO_CVD"
        assert result.asset == "ETH"

    def test_buy_yes_confidence(self, feed_snapshot, turbo_cvd_config):
        snap = replace(feed_snapshot, cvd_2min=300_000.0)
        strat = TurboCvdStrategy(turbo_cvd_config)
        result = strat.evaluate("ETH", snap)
        # 300_000 / (200_000 * 3) = 300_000 / 600_000 = 0.5
        assert result.confidence == pytest.approx(0.5)

    def test_buy_yes_confidence_caps_at_1(self, feed_snapshot, turbo_cvd_config):
        snap = replace(feed_snapshot, cvd_2min=1_000_000.0)
        strat = TurboCvdStrategy(turbo_cvd_config)
        result = strat.evaluate("ETH", snap)
        # 1_000_000 / 600_000 = 1.667 -> capped to 1.0
        assert result.confidence == pytest.approx(1.0)


class TestTurboCvdBuyNo:
    """BUY_NO when cvd < -threshold."""

    def test_buy_no_below_neg_threshold(self, feed_snapshot, turbo_cvd_config):
        snap = replace(feed_snapshot, cvd_2min=-500_000.0)
        strat = TurboCvdStrategy(turbo_cvd_config)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.BUY_NO

    def test_buy_no_confidence(self, feed_snapshot, turbo_cvd_config):
        snap = replace(feed_snapshot, cvd_2min=-300_000.0)
        strat = TurboCvdStrategy(turbo_cvd_config)
        result = strat.evaluate("ETH", snap)
        # abs(-300_000) / 600_000 = 0.5
        assert result.confidence == pytest.approx(0.5)


class TestTurboCvdSkip:
    """SKIP when cvd within [-threshold, +threshold]."""

    def test_skip_cvd_below_threshold(self, feed_snapshot, turbo_cvd_config):
        snap = replace(feed_snapshot, cvd_2min=100_000.0)
        strat = TurboCvdStrategy(turbo_cvd_config)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.SKIP
        assert result.confidence == 0.0

    def test_skip_cvd_zero(self, feed_snapshot, turbo_cvd_config):
        snap = replace(feed_snapshot, cvd_2min=0.0)
        strat = TurboCvdStrategy(turbo_cvd_config)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.SKIP
        assert result.confidence == 0.0
