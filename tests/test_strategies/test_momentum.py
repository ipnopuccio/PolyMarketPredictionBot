"""Tests for MomentumStrategy — CVD + VWAP trend following.

Momentum requires BOTH cvd > threshold AND vwap > threshold to fire.
Defaults: cvd_threshold=1_000_000, vwap_threshold=0.0005.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from bot.core.types import FeedSnapshot, Signal
from bot.strategies.momentum import MomentumStrategy


# ── evaluate(): BUY_YES ────────────────────────────────────────────────


class TestMomentumBuyYes:
    """BUY_YES fires when cvd > thresh AND vwap > vwap_thresh."""

    def test_buy_yes_both_above(self, feed_snapshot, momentum_config):
        """Default snapshot has cvd=1.5M > 1M and vwap=0.00074 > 0.0005."""
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", feed_snapshot)
        assert result.signal == Signal.BUY_YES
        assert result.strategy == "MOMENTUM"
        assert result.asset == "BTC"

    def test_buy_yes_confidence_formula(self, feed_snapshot, momentum_config):
        """Confidence = min(1.0, abs(cvd) / (threshold * 3))."""
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", feed_snapshot)
        # cvd=1_500_000, threshold=1_000_000 -> 1_500_000 / 3_000_000 = 0.5
        assert result.confidence == pytest.approx(0.5)

    def test_buy_yes_confidence_caps_at_1(self, feed_snapshot, momentum_config):
        """CVD of 4M saturates confidence to 1.0."""
        snap = replace(feed_snapshot, cvd_2min=4_000_000.0)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.BUY_YES
        # 4_000_000 / 3_000_000 = 1.333 -> capped to 1.0
        assert result.confidence == pytest.approx(1.0)

    def test_buy_yes_exactly_at_saturation(self, feed_snapshot, momentum_config):
        """CVD = 3 * threshold -> confidence exactly 1.0."""
        snap = replace(feed_snapshot, cvd_2min=3_000_000.0)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.BUY_YES
        assert result.confidence == pytest.approx(1.0)

    def test_buy_yes_includes_indicators(self, feed_snapshot, momentum_config):
        """Result indicators contain cvd and vwap_change."""
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", feed_snapshot)
        assert result.indicators["cvd"] == feed_snapshot.cvd_2min
        assert result.indicators["vwap_change"] == feed_snapshot.vwap_change


# ── evaluate(): BUY_NO ─────────────────────────────────────────────────


class TestMomentumBuyNo:
    """BUY_NO fires when cvd < -thresh AND vwap < -vwap_thresh."""

    def test_buy_no_both_below(self, feed_snapshot, momentum_config):
        snap = replace(feed_snapshot, cvd_2min=-1_500_000.0, vwap_change=-0.001)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.BUY_NO

    def test_buy_no_confidence_uses_abs_cvd(self, feed_snapshot, momentum_config):
        snap = replace(feed_snapshot, cvd_2min=-1_500_000.0, vwap_change=-0.001)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        # abs(-1_500_000) / 3_000_000 = 0.5
        assert result.confidence == pytest.approx(0.5)

    def test_buy_no_large_cvd(self, feed_snapshot, momentum_config):
        snap = replace(feed_snapshot, cvd_2min=-5_000_000.0, vwap_change=-0.01)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.BUY_NO
        assert result.confidence == pytest.approx(1.0)


# ── evaluate(): SKIP ───────────────────────────────────────────────────


class TestMomentumSkip:
    """SKIP when only one or neither condition is met."""

    def test_skip_cvd_above_vwap_below(self, feed_snapshot, momentum_config):
        """CVD positive but VWAP negative -> SKIP."""
        snap = replace(feed_snapshot, cvd_2min=2_000_000.0, vwap_change=-0.001)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.SKIP
        assert result.confidence == 0.0

    def test_skip_vwap_above_cvd_below(self, feed_snapshot, momentum_config):
        """VWAP positive but CVD below threshold -> SKIP."""
        snap = replace(feed_snapshot, cvd_2min=500_000.0, vwap_change=0.001)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.SKIP

    def test_skip_both_below_threshold(self, feed_snapshot, momentum_config):
        """Both below threshold -> SKIP."""
        snap = replace(feed_snapshot, cvd_2min=100_000.0, vwap_change=0.0001)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.SKIP

    def test_skip_zero_values(self, feed_snapshot, momentum_config):
        snap = replace(feed_snapshot, cvd_2min=0.0, vwap_change=0.0)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.SKIP
        assert result.confidence == 0.0

    def test_skip_opposite_directions(self, feed_snapshot, momentum_config):
        """CVD negative but VWAP positive (divergence) -> SKIP."""
        snap = replace(feed_snapshot, cvd_2min=-2_000_000.0, vwap_change=0.001)
        strat = MomentumStrategy(momentum_config)
        result = strat.evaluate("BTC", snap)
        assert result.signal == Signal.SKIP


# ── entry_ok() ──────────────────────────────────────────────────────────


class TestMomentumEntryOk:
    """Entry price guards: BUY_YES max 0.55, BUY_NO max 0.75, SKIP False."""

    def test_entry_ok_buy_yes_under_max(self, momentum_config):
        strat = MomentumStrategy(momentum_config)
        assert strat.entry_ok("BTC", Signal.BUY_YES, 0.50) is True

    def test_entry_ok_buy_yes_at_max(self, momentum_config):
        strat = MomentumStrategy(momentum_config)
        assert strat.entry_ok("BTC", Signal.BUY_YES, 0.55) is True

    def test_entry_reject_buy_yes_over_max(self, momentum_config):
        strat = MomentumStrategy(momentum_config)
        assert strat.entry_ok("BTC", Signal.BUY_YES, 0.56) is False

    def test_entry_ok_buy_no_under_max(self, momentum_config):
        strat = MomentumStrategy(momentum_config)
        assert strat.entry_ok("BTC", Signal.BUY_NO, 0.70) is True

    def test_entry_reject_buy_no_over_max(self, momentum_config):
        strat = MomentumStrategy(momentum_config)
        assert strat.entry_ok("BTC", Signal.BUY_NO, 0.80) is False

    def test_entry_skip_always_false(self, momentum_config):
        strat = MomentumStrategy(momentum_config)
        assert strat.entry_ok("BTC", Signal.SKIP, 0.10) is False
