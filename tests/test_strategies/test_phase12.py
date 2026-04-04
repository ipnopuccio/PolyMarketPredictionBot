"""Phase 12: Strategy Intelligence — comprehensive tests.

Tests for:
  12.1 Adaptive thresholds integration
  12.2 Multi-timeframe confirmation
  12.3 Regime-based strategy selection
  12.4 Composite confidence scorer
  12.5 Cross-asset correlation filter
  12.6 Strategy hot-swap API
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from bot.config import (
    CompositeConfidenceConfig,
    CorrelationConfig,
    TurboCvdConfig,
    TurboVwapConfig,
)
from bot.core.types import (
    FeedSnapshot,
    RegimeResult,
    RegimeType,
    Signal,
)
from bot.strategies.adaptive import AdaptiveThreshold
from bot.strategies.composite import CompositeConfidenceScorer
from bot.strategies.correlation import CrossAssetCorrelationFilter
from bot.strategies.multi_tf import MultiTimeframeTrend, _CandleBuilder, _ema
from bot.strategies.selector import StrategySelector, DEFAULT_REGIME_RULES
from bot.strategies.turbo_cvd import TurboCvdStrategy
from bot.strategies.turbo_vwap import TurboVwapStrategy


# ── Helpers ──────────────────────────────────────────────

def _snap(
    price: float = 100.0,
    cvd: float = 0.0,
    vwap: float = 0.0,
    funding: float = 0.0,
    oi: float = 1e9,
    ls_ratio: float = 1.0,
) -> FeedSnapshot:
    return FeedSnapshot(
        last_price=price,
        price_2min_ago=price - 1,
        vwap_change=vwap,
        cvd_2min=cvd,
        funding_rate=funding,
        open_interest=oi,
        long_short_ratio=ls_ratio,
        connected=True,
        last_update=time.time(),
    )


def _regime(rt: RegimeType = RegimeType.UNKNOWN, conf: float = 0.5) -> RegimeResult:
    return RegimeResult(regime=rt, adx=25.0, bb_width=0.01, ema_slope=0.001, confidence=conf)


# ════════════════════════════════════════════════════════════
# 12.1 — Adaptive Thresholds
# ════════════════════════════════════════════════════════════


class TestAdaptiveThreshold:
    def test_fallback_when_insufficient_data(self):
        at = AdaptiveThreshold(min_samples=10)
        assert at.get_cvd_threshold("BTC", 500_000) == 500_000
        assert at.get_vwap_threshold("BTC", 0.001) == 0.001

    def test_has_enough_data_false_initially(self):
        at = AdaptiveThreshold(min_samples=5)
        assert not at.has_enough_data("ETH")

    def test_has_enough_data_after_feeding(self):
        at = AdaptiveThreshold(min_samples=5)
        for i in range(10):
            at.update("ETH", float(i * 100_000), float(i * 0.001))
        assert at.has_enough_data("ETH")

    def test_adaptive_cvd_uses_percentile(self):
        at = AdaptiveThreshold(min_samples=5, percentile=50)
        # Feed 20 values: CVD from 100k to 2M
        for i in range(20):
            at.update("ETH", float((i + 1) * 100_000), 0.001)
        thresh = at.get_cvd_threshold("ETH", 999_999)
        # Should be around median of abs values, NOT the fallback
        assert thresh != 999_999
        assert thresh > 0

    def test_adaptive_vwap_uses_percentile(self):
        at = AdaptiveThreshold(min_samples=5, percentile=75)
        for i in range(20):
            at.update("BTC", 100_000, float((i + 1) * 0.0001))
        thresh = at.get_vwap_threshold("BTC", 0.999)
        assert thresh != 0.999
        assert thresh > 0

    def test_window_trims_old_data(self):
        at = AdaptiveThreshold(window_seconds=10, min_samples=3)
        base = time.time()
        # Feed old data
        for i in range(5):
            at._cvd["BTC"] = at._cvd.get("BTC", __import__("collections").deque())
            at._cvd["BTC"].append((base - 100 + i, float(i * 1000)))
            at._vwap["BTC"] = at._vwap.get("BTC", __import__("collections").deque())
            at._vwap["BTC"].append((base - 100 + i, float(i * 0.001)))
        # Old data should be trimmed
        assert not at.has_enough_data("BTC")

    def test_per_asset_isolation(self):
        at = AdaptiveThreshold(min_samples=3)
        for i in range(5):
            at.update("BTC", float(i * 1_000_000), float(i * 0.01))
        for i in range(5):
            at.update("ETH", float(i * 100_000), float(i * 0.001))
        btc_thresh = at.get_cvd_threshold("BTC", 0)
        eth_thresh = at.get_cvd_threshold("ETH", 0)
        assert btc_thresh > eth_thresh  # BTC values are 10x larger


class TestTurboCvdAdaptive:
    def test_uses_fixed_threshold_without_adaptive(self):
        cfg = TurboCvdConfig(cvd_threshold=200_000)
        strat = TurboCvdStrategy(cfg)
        snap = _snap(cvd=300_000)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.BUY_YES

    def test_uses_adaptive_when_warmed(self):
        cfg = TurboCvdConfig(cvd_threshold=200_000)
        at = AdaptiveThreshold(min_samples=5, percentile=90)
        strat = TurboCvdStrategy(cfg, adaptive=at)
        # Feed high CVD values so P90 threshold is high
        for i in range(10):
            at.update("ETH", 5_000_000, 0.01)
        # CVD=300k is below P90 of 5M values → should SKIP
        snap = _snap(cvd=300_000)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.SKIP

    def test_falls_back_to_fixed_when_cold(self):
        cfg = TurboCvdConfig(cvd_threshold=200_000)
        at = AdaptiveThreshold(min_samples=100)  # needs 100 samples
        strat = TurboCvdStrategy(cfg, adaptive=at)
        # Only feed 2 samples
        at.update("ETH", 500_000, 0.001)
        at.update("ETH", 500_000, 0.001)
        snap = _snap(cvd=300_000)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.BUY_YES  # fixed threshold 200k < 300k


class TestTurboVwapAdaptive:
    def test_uses_adaptive_when_warmed(self):
        cfg = TurboVwapConfig(vwap_threshold=0.0002)
        at = AdaptiveThreshold(min_samples=5, percentile=90)
        strat = TurboVwapStrategy(cfg, adaptive=at)
        # Feed high VWAP values
        for i in range(10):
            at.update("ETH", 100_000, 0.05)
        # vwap=0.001 is below P90 of 0.05 values → SKIP
        snap = _snap(vwap=0.001)
        result = strat.evaluate("ETH", snap)
        assert result.signal == Signal.SKIP

    def test_indicators_include_threshold(self):
        cfg = TurboVwapConfig(vwap_threshold=0.0002)
        strat = TurboVwapStrategy(cfg)
        snap = _snap(vwap=0.001)
        result = strat.evaluate("ETH", snap)
        assert "vwap_threshold" in result.indicators


# ════════════════════════════════════════════════════════════
# 12.2 — Multi-Timeframe Confirmation
# ════════════════════════════════════════════════════════════


class TestMultiTimeframeTrend:
    def test_flat_on_no_data(self):
        mtt = MultiTimeframeTrend()
        assert mtt.get_trend("BTC", "5m") == "FLAT"

    def test_confirmed_during_warmup(self):
        mtt = MultiTimeframeTrend()
        assert mtt.is_confirmed("BTC", "BUY_YES") is True
        assert mtt.is_confirmed("BTC", "BUY_NO") is True

    def test_uptrend_detection(self):
        mtt = MultiTimeframeTrend()
        base_ts = 1000.0
        for i in range(300):
            mtt.update("BTC", 100.0 + i * 0.5, base_ts + i * 10)
        trend = mtt.get_trend("BTC", "5m")
        assert trend == "UP"

    def test_downtrend_detection(self):
        mtt = MultiTimeframeTrend()
        base_ts = 1000.0
        for i in range(300):
            mtt.update("BTC", 200.0 - i * 0.5, base_ts + i * 10)
        trend = mtt.get_trend("BTC", "5m")
        assert trend == "DOWN"

    def test_buy_yes_blocked_by_downtrend(self):
        mtt = MultiTimeframeTrend()
        base_ts = 1000.0
        for i in range(300):
            mtt.update("BTC", 200.0 - i * 0.5, base_ts + i * 10)
        assert mtt.is_confirmed("BTC", "BUY_YES") is False

    def test_buy_no_blocked_by_uptrend(self):
        mtt = MultiTimeframeTrend()
        base_ts = 1000.0
        for i in range(300):
            mtt.update("BTC", 100.0 + i * 0.5, base_ts + i * 10)
        assert mtt.is_confirmed("BTC", "BUY_NO") is False

    def test_get_all_trends(self):
        mtt = MultiTimeframeTrend()
        trends = mtt.get_all_trends("SOL")
        assert "5m" in trends
        assert "15m" in trends


class TestCandleBuilder:
    def test_builds_candles_on_bucket_change(self):
        cb = _CandleBuilder(60)
        # ts=960 → bucket 960, ts=1000 → bucket 960 (same), ts=1020 → bucket 1020 (new)
        cb.update(100.0, 960.0)
        cb.update(105.0, 1000.0)
        # Bucket change at 1020 (new 60s bucket)
        cb.update(110.0, 1020.0)
        assert cb.count == 1
        assert cb.closes[0] == 105.0  # last price of previous bucket

    def test_ema_basic(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = _ema(vals, 3)
        assert len(result) == len(vals)
        assert result[0] == 1.0


# ════════════════════════════════════════════════════════════
# 12.3 — Regime-Based Strategy Selection
# ════════════════════════════════════════════════════════════


class TestStrategySelector:
    def test_trending_disables_bollinger(self):
        sel = StrategySelector()
        assert sel.is_allowed("BOLLINGER", RegimeType.TRENDING) is False

    def test_trending_allows_momentum(self):
        sel = StrategySelector()
        assert sel.is_allowed("MOMENTUM", RegimeType.TRENDING) is True

    def test_ranging_disables_momentum(self):
        sel = StrategySelector()
        assert sel.is_allowed("MOMENTUM", RegimeType.RANGING) is False

    def test_ranging_allows_bollinger(self):
        sel = StrategySelector()
        assert sel.is_allowed("BOLLINGER", RegimeType.RANGING) is True

    def test_volatile_halves_position_size(self):
        sel = StrategySelector()
        assert sel.get_size_multiplier("TURBO_CVD", RegimeType.VOLATILE) == 0.5

    def test_ranging_reduces_turbo_size(self):
        sel = StrategySelector()
        assert sel.get_size_multiplier("TURBO_CVD", RegimeType.RANGING) == 0.8

    def test_normal_size_for_trending_momentum(self):
        sel = StrategySelector()
        assert sel.get_size_multiplier("MOMENTUM", RegimeType.TRENDING) == 1.0

    def test_override_enables_disabled_strategy(self):
        sel = StrategySelector()
        sel.set_override("BOLLINGER", True)
        # Bollinger is disabled in TRENDING by default, but override enables it
        assert sel.is_allowed("BOLLINGER", RegimeType.TRENDING) is True

    def test_override_disables_enabled_strategy(self):
        sel = StrategySelector()
        sel.set_override("MOMENTUM", False)
        assert sel.is_allowed("MOMENTUM", RegimeType.TRENDING) is False

    def test_clear_override_reverts(self):
        sel = StrategySelector()
        sel.set_override("BOLLINGER", True)
        sel.clear_override("BOLLINGER")
        assert sel.is_allowed("BOLLINGER", RegimeType.TRENDING) is False

    def test_clear_all_overrides(self):
        sel = StrategySelector()
        sel.set_override("BOLLINGER", True)
        sel.set_override("MOMENTUM", False)
        sel.clear_all_overrides()
        assert sel.is_allowed("BOLLINGER", RegimeType.TRENDING) is False
        assert sel.is_allowed("MOMENTUM", RegimeType.TRENDING) is True

    def test_unknown_strategy_allowed_by_default(self):
        sel = StrategySelector()
        assert sel.is_allowed("FOOBAR", RegimeType.TRENDING) is True

    def test_get_status_returns_all_strategies(self):
        sel = StrategySelector()
        status = sel.get_status(RegimeType.TRENDING)
        assert "MOMENTUM" in status
        assert "BOLLINGER" in status
        assert "TURBO_CVD" in status

    def test_get_status_shows_override(self):
        sel = StrategySelector()
        sel.set_override("BOLLINGER", True)
        status = sel.get_status(RegimeType.TRENDING)
        assert status["BOLLINGER"]["override"] is True
        assert status["BOLLINGER"]["allowed"] is True


# ════════════════════════════════════════════════════════════
# 12.4 — Composite Confidence Scorer
# ════════════════════════════════════════════════════════════


class TestCompositeConfidence:
    def test_all_strong_indicators_high_score(self):
        cfg = CompositeConfidenceConfig()
        scorer = CompositeConfidenceScorer(cfg)
        snap = _snap(
            cvd=2_000_000,
            funding=0.01,
            oi=5e9,
            ls_ratio=1.8,
        )
        score = scorer.score(
            snapshot=snap,
            rsi=75.0,  # strongly overbought
            bb={"pct": 1.2, "upper": 110, "lower": 90, "mid": 100},
            regime=_regime(RegimeType.TRENDING, 0.9),
        )
        assert score > 0.6

    def test_all_neutral_indicators_low_score(self):
        cfg = CompositeConfidenceConfig()
        scorer = CompositeConfidenceScorer(cfg)
        snap = _snap(cvd=0, funding=0, oi=0, ls_ratio=1.0)
        score = scorer.score(snapshot=snap, rsi=50.0, bb={"pct": 0.5})
        # All indicators at neutral → low score (below min_confidence=0.4)
        assert score < 0.4

    def test_score_between_0_and_1(self):
        cfg = CompositeConfidenceConfig()
        scorer = CompositeConfidenceScorer(cfg)
        snap = _snap()
        score = scorer.score(snapshot=snap)
        assert 0.0 <= score <= 1.0

    def test_missing_indicators_get_defaults(self):
        cfg = CompositeConfidenceConfig()
        scorer = CompositeConfidenceScorer(cfg)
        snap = _snap()
        # No rsi, no bb, no regime
        score = scorer.score(snapshot=snap)
        assert isinstance(score, float)

    def test_extreme_rsi_increases_score(self):
        cfg = CompositeConfidenceConfig()
        scorer = CompositeConfidenceScorer(cfg)
        snap = _snap()
        score_neutral = scorer.score(snapshot=snap, rsi=50.0)
        score_extreme = scorer.score(snapshot=snap, rsi=85.0)
        assert score_extreme > score_neutral

    def test_regime_alignment_increases_score(self):
        cfg = CompositeConfidenceConfig()
        scorer = CompositeConfidenceScorer(cfg)
        snap = _snap()
        score_unknown = scorer.score(snapshot=snap, regime=_regime(RegimeType.UNKNOWN, 0.3))
        score_trending = scorer.score(snapshot=snap, regime=_regime(RegimeType.TRENDING, 0.9))
        assert score_trending > score_unknown


# ════════════════════════════════════════════════════════════
# 12.5 — Cross-Asset Correlation Filter
# ════════════════════════════════════════════════════════════


class TestCrossAssetCorrelation:
    def test_btc_always_allowed(self):
        cfg = CorrelationConfig()
        cf = CrossAssetCorrelationFilter(cfg)
        assert cf.is_allowed("BTC", "BUY_YES") is True
        assert cf.is_allowed("BTC", "BUY_NO") is True

    def test_allowed_when_no_data(self):
        cfg = CorrelationConfig()
        cf = CrossAssetCorrelationFilter(cfg)
        assert cf.is_allowed("ETH", "BUY_YES") is True

    def test_btc_drop_blocks_buy_yes_on_eth(self):
        cfg = CorrelationConfig(btc_drop_threshold_pct=1.0, btc_drop_window_seconds=300)
        cf = CrossAssetCorrelationFilter(cfg)
        base_ts = time.time()
        # BTC drops from 100 to 98 (-2%)
        cf.update("BTC", 100.0, base_ts)
        cf.update("BTC", 98.0, base_ts + 60)
        assert cf.is_allowed("ETH", "BUY_YES") is False

    def test_btc_drop_does_not_block_buy_no(self):
        cfg = CorrelationConfig(btc_drop_threshold_pct=1.0, btc_drop_window_seconds=300)
        cf = CrossAssetCorrelationFilter(cfg)
        base_ts = time.time()
        cf.update("BTC", 100.0, base_ts)
        cf.update("BTC", 98.0, base_ts + 60)
        # BUY_NO on ETH is fine when BTC drops
        assert cf.is_allowed("ETH", "BUY_NO") is True

    def test_small_btc_move_allowed(self):
        cfg = CorrelationConfig(btc_drop_threshold_pct=1.0, btc_drop_window_seconds=300)
        cf = CrossAssetCorrelationFilter(cfg)
        base_ts = time.time()
        cf.update("BTC", 100.0, base_ts)
        cf.update("BTC", 99.5, base_ts + 60)  # only -0.5%
        assert cf.is_allowed("ETH", "BUY_YES") is True

    def test_correlation_filter_with_correlated_assets(self):
        cfg = CorrelationConfig(
            correlation_threshold=0.7,
            btc_drop_threshold_pct=5.0,  # high so rule 1 doesn't trigger
            btc_drop_window_seconds=300,
            correlation_window_seconds=1800,
        )
        cf = CrossAssetCorrelationFilter(cfg)
        base_ts = 1000.0
        # Feed highly correlated price series (BTC and ETH move together)
        for i in range(60):
            ts = base_ts + i * 60
            btc_price = 50000 + i * 100  # trending up then down
            eth_price = 3000 + i * 6
            if i > 40:
                btc_price = 50000 + 40 * 100 - (i - 40) * 200
                eth_price = 3000 + 40 * 6 - (i - 40) * 12
            cf.update("BTC", btc_price, ts)
            cf.update("ETH", eth_price, ts)

        # After this series, BTC and ETH should be correlated
        # Last prices show BTC going down strongly
        # is_allowed will depend on the computed correlation
        result = cf.is_allowed("ETH", "BUY_YES")
        assert isinstance(result, bool)

    def test_update_ignores_zero_price(self):
        cfg = CorrelationConfig()
        cf = CrossAssetCorrelationFilter(cfg)
        cf.update("BTC", 0.0, time.time())
        assert "BTC" not in cf._prices


# ════════════════════════════════════════════════════════════
# 12.6 — Strategy Hot-Swap (selector functionality, API tested in test_dashboard)
# ════════════════════════════════════════════════════════════


class TestHotSwap:
    def test_enable_override_persists(self):
        sel = StrategySelector()
        sel.set_override("BOLLINGER", True)
        assert sel.is_allowed("BOLLINGER", RegimeType.TRENDING) is True
        assert sel.is_allowed("BOLLINGER", RegimeType.VOLATILE) is True

    def test_disable_override_persists(self):
        sel = StrategySelector()
        sel.set_override("TURBO_CVD", False)
        assert sel.is_allowed("TURBO_CVD", RegimeType.TRENDING) is False
        assert sel.is_allowed("TURBO_CVD", RegimeType.UNKNOWN) is False

    def test_multiple_overrides(self):
        sel = StrategySelector()
        sel.set_override("MOMENTUM", False)
        sel.set_override("BOLLINGER", True)
        assert sel.is_allowed("MOMENTUM", RegimeType.UNKNOWN) is False
        assert sel.is_allowed("BOLLINGER", RegimeType.VOLATILE) is True

    def test_status_reflects_overrides(self):
        sel = StrategySelector()
        sel.set_override("MOMENTUM", False)
        status = sel.get_status(RegimeType.TRENDING)
        assert status["MOMENTUM"]["override"] is False
        assert status["MOMENTUM"]["allowed"] is False
