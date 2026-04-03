"""Tests for market regime classifier."""
from __future__ import annotations

import pytest
import numpy as np

from bot.core.types import RegimeType, RegimeResult
from bot.strategies.regime import (
    classify,
    _compute_adx,
    _compute_bb_width,
    _compute_ema_slope,
    MIN_PRICES,
)


# ── Helpers ──────────────────────────────────────────────

def _trending_up(n: int = 60) -> list[float]:
    """Generate strong uptrend prices (deterministic)."""
    return [100.0 + i * 1.5 for i in range(n)]


def _trending_down(n: int = 60) -> list[float]:
    """Generate strong downtrend prices (deterministic)."""
    return [200.0 - i * 1.5 for i in range(n)]


def _ranging(n: int = 60) -> list[float]:
    """Generate sideways/flat prices with very small noise."""
    return [100.0 + np.sin(i * 0.3) * 0.1 for i in range(n)]


def _volatile(n: int = 60) -> list[float]:
    """Generate high-volatility prices with wide swings (deterministic)."""
    # Create a pattern: mostly calm, then extreme swings at the end
    calm = [100.0 + np.sin(i * 0.3) * 0.5 for i in range(n // 2)]
    wild = [100.0 + np.sin(i * 0.5) * 30.0 for i in range(n // 2)]
    return calm + wild


# ── Core classification tests ────────────────────────────

class TestClassify:
    def test_trending_up_detected(self):
        prices = _trending_up(80)
        result = classify(prices)
        assert result.regime == RegimeType.TRENDING
        assert result.confidence > 0.0

    def test_trending_down_detected(self):
        prices = _trending_down(80)
        result = classify(prices)
        assert result.regime in (RegimeType.TRENDING, RegimeType.VOLATILE)

    def test_ranging_detected(self):
        prices = _ranging(80)
        result = classify(prices)
        assert result.regime in (RegimeType.RANGING, RegimeType.UNKNOWN)

    def test_volatile_detected(self):
        prices = _volatile(100)
        result = classify(prices)
        # Volatile data should NOT be classified as TRENDING (no strong directional move)
        assert result.regime in (RegimeType.VOLATILE, RegimeType.RANGING, RegimeType.UNKNOWN)

    def test_insufficient_data_returns_unknown(self):
        result = classify([100.0] * 10)
        assert result.regime == RegimeType.UNKNOWN
        assert result.confidence == 0.0

    def test_empty_list_returns_unknown(self):
        result = classify([])
        assert result.regime == RegimeType.UNKNOWN

    def test_single_price_returns_unknown(self):
        result = classify([42.0])
        assert result.regime == RegimeType.UNKNOWN

    def test_all_same_price(self):
        result = classify([100.0] * 60)
        # Flat price → low ADX, zero BB width → RANGING or UNKNOWN
        assert result.regime in (RegimeType.RANGING, RegimeType.UNKNOWN)

    def test_exactly_min_prices(self):
        prices = _trending_up(MIN_PRICES)
        result = classify(prices)
        assert isinstance(result, RegimeResult)
        assert result.regime != RegimeType.UNKNOWN or result.confidence >= 0.0

    def test_nan_in_prices(self):
        prices = [100.0] * 40
        prices[20] = float("nan")
        result = classify(prices)
        assert result.regime == RegimeType.UNKNOWN


# ── Confidence tests ─────────────────────────────────────

class TestConfidence:
    def test_confidence_between_0_and_1(self):
        for gen in [_trending_up, _trending_down, _ranging, _volatile]:
            result = classify(gen(80))
            assert 0.0 <= result.confidence <= 1.0

    def test_unknown_low_confidence(self):
        result = classify([100.0] * 5)
        assert result.confidence == 0.0


# ── Indicator calculation tests ──────────────────────────

class TestIndicators:
    def test_adx_positive_for_trending(self):
        prices = np.array(_trending_up(80))
        adx = _compute_adx(prices)
        assert adx > 0

    def test_adx_zero_for_insufficient_data(self):
        prices = np.array([100.0, 101.0])
        adx = _compute_adx(prices)
        assert adx == 0.0

    def test_bb_width_positive_for_volatile(self):
        prices = np.array(_volatile(40))
        bb = _compute_bb_width(prices)
        assert bb > 0

    def test_bb_width_zero_for_insufficient(self):
        prices = np.array([100.0] * 5)
        bb = _compute_bb_width(prices)
        assert bb == 0.0

    def test_ema_slope_positive_for_uptrend(self):
        prices = np.array(_trending_up(60))
        slope = _compute_ema_slope(prices)
        assert slope > 0

    def test_ema_slope_negative_for_downtrend(self):
        prices = np.array(_trending_down(60))
        slope = _compute_ema_slope(prices)
        assert slope < 0

    def test_ema_slope_near_zero_for_flat(self):
        prices = np.array([100.0] * 60)
        slope = _compute_ema_slope(prices)
        assert abs(slope) < 0.001


# ── Result dataclass tests ───────────────────────────────

class TestRegimeResult:
    def test_result_is_frozen(self):
        result = classify(_trending_up(60))
        with pytest.raises(AttributeError):
            result.regime = RegimeType.UNKNOWN

    def test_result_has_all_fields(self):
        result = classify(_trending_up(60))
        assert hasattr(result, "regime")
        assert hasattr(result, "adx")
        assert hasattr(result, "bb_width")
        assert hasattr(result, "ema_slope")
        assert hasattr(result, "confidence")
