"""Market regime classifier using ADX, Bollinger Band width, and EMA slope.

Classifies the current market into one of three regimes:
- TRENDING: Strong directional movement (ADX > 25, significant EMA slope)
- RANGING: Low volatility sideways action (ADX < 20, narrow BB width)
- VOLATILE: High volatility / wide price swings (BB width > 1.5x median)

Used BY strategies to adapt parameters — this is NOT a strategy subclass.
"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

from bot.core.types import RegimeResult, RegimeType

logger = logging.getLogger(__name__)

# Minimum prices required for a valid classification
MIN_PRICES = 30

# ADX thresholds
ADX_TRENDING_THRESHOLD = 25.0
ADX_RANGING_THRESHOLD = 20.0

# ADX smoothing period
ADX_PERIOD = 14

# Bollinger Band / EMA parameters
BB_PERIOD = 20
BB_STD_MULT = 2.0
EMA_PERIOD = 20
EMA_SLOPE_LOOKBACK = 5

# EMA slope threshold (rate of change as fraction of price)
EMA_SLOPE_THRESHOLD = 0.0005

# BB width multiplier for VOLATILE detection
BB_VOLATILE_MULT = 1.5

# Unknown result returned when data is insufficient
UNKNOWN_RESULT = RegimeResult(
    regime=RegimeType.UNKNOWN,
    adx=0.0,
    bb_width=0.0,
    ema_slope=0.0,
    confidence=0.0,
)


def classify(prices: Sequence[float]) -> RegimeResult:
    """Classify the market regime from a price history.

    Args:
        prices: List of prices (most recent last), at least 30 required.

    Returns:
        RegimeResult with regime type, indicator values, and confidence.
    """
    if len(prices) < MIN_PRICES:
        logger.debug("Insufficient prices for regime detection: %d < %d", len(prices), MIN_PRICES)
        return UNKNOWN_RESULT

    arr = np.array(prices, dtype=np.float64)

    # Guard against degenerate data (all zeros, all NaN, etc.)
    if np.any(np.isnan(arr)) or np.any(np.isinf(arr)):
        logger.warning("Price data contains NaN or Inf values")
        return UNKNOWN_RESULT

    adx = _compute_adx(arr, ADX_PERIOD)
    bb_width = _compute_bb_width(arr, BB_PERIOD, BB_STD_MULT)
    ema_slope = _compute_ema_slope(arr, EMA_PERIOD, EMA_SLOPE_LOOKBACK)

    # Median BB width over the full history for relative comparison
    bb_width_median = _compute_bb_width_median(arr, BB_PERIOD, BB_STD_MULT)

    regime, confidence = _determine_regime(adx, bb_width, bb_width_median, ema_slope)

    logger.debug(
        "Regime=%s ADX=%.2f BB_W=%.6f EMA_S=%.6f conf=%.2f",
        regime.value, adx, bb_width, ema_slope, confidence,
    )

    return RegimeResult(
        regime=regime,
        adx=round(adx, 4),
        bb_width=round(bb_width, 6),
        ema_slope=round(ema_slope, 6),
        confidence=round(min(1.0, max(0.0, confidence)), 4),
    )


# ---------------------------------------------------------------------------
# ADX (Average Directional Index) — standard 14-period
# ---------------------------------------------------------------------------

def _compute_adx(prices: np.ndarray, period: int = 14) -> float:
    """Compute ADX from price array using the standard Wilder smoothing.

    Since we only have close prices (no high/low), we approximate:
    - High ~ max(close[i], close[i-1])
    - Low  ~ min(close[i], close[i-1])
    This is a common approximation when only close prices are available.
    """
    n = len(prices)
    if n < period + 1:
        return 0.0

    # Approximate high/low from consecutive closes
    highs = np.maximum(prices[1:], prices[:-1])
    lows = np.minimum(prices[1:], prices[:-1])

    # True Range
    prev_close = prices[:-1]
    tr = np.maximum(
        highs - lows,
        np.maximum(
            np.abs(highs - prev_close),
            np.abs(lows - prev_close),
        ),
    )

    # Directional Movement
    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Align TR with DM (TR is len n-1, DM is len n-2)
    tr_aligned = tr[1:]

    if len(tr_aligned) < period:
        return 0.0

    # Wilder smoothing (EMA with alpha = 1/period)
    atr = _wilder_smooth(tr_aligned, period)
    plus_di_smooth = _wilder_smooth(plus_dm, period)
    minus_di_smooth = _wilder_smooth(minus_dm, period)

    if len(atr) == 0:
        return 0.0

    # +DI and -DI as percentages
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = np.where(atr > 0, 100.0 * plus_di_smooth / atr, 0.0)
        minus_di = np.where(atr > 0, 100.0 * minus_di_smooth / atr, 0.0)

    # DX
    di_sum = plus_di + minus_di
    with np.errstate(divide="ignore", invalid="ignore"):
        dx = np.where(di_sum > 0, 100.0 * np.abs(plus_di - minus_di) / di_sum, 0.0)

    # ADX = Wilder smooth of DX
    if len(dx) < period:
        return float(np.mean(dx)) if len(dx) > 0 else 0.0

    adx_series = _wilder_smooth(dx, period)
    return float(adx_series[-1]) if len(adx_series) > 0 else 0.0


def _wilder_smooth(data: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing method (equivalent to EMA with alpha = 1/period)."""
    if len(data) < period:
        return data

    result = np.empty(len(data) - period + 1)
    result[0] = np.mean(data[:period])

    alpha = 1.0 / period
    for i in range(1, len(result)):
        result[i] = result[i - 1] * (1 - alpha) + data[period - 1 + i] * alpha

    return result


# ---------------------------------------------------------------------------
# Bollinger Band Width
# ---------------------------------------------------------------------------

def _compute_bb_width(prices: np.ndarray, period: int = 20, std_mult: float = 2.0) -> float:
    """Compute current BB width as a fraction of the middle band (SMA)."""
    if len(prices) < period:
        return 0.0

    window = prices[-period:]
    sma = float(np.mean(window))
    if sma == 0:
        return 0.0

    std = float(np.std(window, ddof=0))
    width = 2.0 * std_mult * std
    return width / sma


def _compute_bb_width_median(prices: np.ndarray, period: int = 20, std_mult: float = 2.0) -> float:
    """Compute the median BB width over rolling windows for relative comparison."""
    n = len(prices)
    if n < period:
        return 0.0

    widths = []
    for i in range(period, n + 1):
        window = prices[i - period:i]
        sma = float(np.mean(window))
        if sma == 0:
            continue
        std = float(np.std(window, ddof=0))
        w = 2.0 * std_mult * std / sma
        widths.append(w)

    return float(np.median(widths)) if widths else 0.0


# ---------------------------------------------------------------------------
# EMA Slope
# ---------------------------------------------------------------------------

def _compute_ema_slope(prices: np.ndarray, period: int = 20, lookback: int = 5) -> float:
    """Compute the rate of change of EMA-20 over the last `lookback` periods.

    Returns the slope as a fraction of the current EMA value (normalized).
    """
    if len(prices) < period + lookback:
        return 0.0

    ema = _ema(prices, period)
    if len(ema) < lookback + 1:
        return 0.0

    ema_now = ema[-1]
    ema_prev = ema[-lookback - 1]

    if ema_prev == 0:
        return 0.0

    # Normalized slope: change per period as fraction of price
    return (ema_now - ema_prev) / (ema_prev * lookback)


def _ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Compute EMA series."""
    if len(prices) < period:
        return np.array([])

    alpha = 2.0 / (period + 1)
    result = np.empty(len(prices) - period + 1)
    result[0] = np.mean(prices[:period])

    for i in range(1, len(result)):
        result[i] = prices[period - 1 + i] * alpha + result[i - 1] * (1 - alpha)

    return result


# ---------------------------------------------------------------------------
# Regime determination logic
# ---------------------------------------------------------------------------

def _determine_regime(
    adx: float,
    bb_width: float,
    bb_width_median: float,
    ema_slope: float,
) -> tuple[RegimeType, float]:
    """Apply classification rules and compute confidence.

    Priority order:
    1. VOLATILE — BB width > 1.5x median (overrides trend detection)
    2. TRENDING — ADX > 25 AND abs(EMA slope) > threshold
    3. RANGING  — ADX < 20 AND BB width < median
    4. UNKNOWN  — ambiguous / transitional
    """
    # 1. VOLATILE: high BB width relative to median
    if bb_width_median > 0 and bb_width > BB_VOLATILE_MULT * bb_width_median:
        ratio = bb_width / (BB_VOLATILE_MULT * bb_width_median)
        confidence = min(1.0, 0.5 + 0.5 * (ratio - 1.0))
        return RegimeType.VOLATILE, confidence

    # 2. TRENDING: strong ADX + meaningful EMA slope
    if adx > ADX_TRENDING_THRESHOLD and abs(ema_slope) > EMA_SLOPE_THRESHOLD:
        # Confidence from ADX strength (25-50 range mapped to 0.5-1.0)
        adx_conf = min(1.0, (adx - ADX_TRENDING_THRESHOLD) / 25.0 + 0.5)
        slope_conf = min(1.0, abs(ema_slope) / (EMA_SLOPE_THRESHOLD * 5))
        confidence = (adx_conf + slope_conf) / 2
        return RegimeType.TRENDING, confidence

    # 3. RANGING: low ADX + narrow bands
    if adx < ADX_RANGING_THRESHOLD and bb_width_median > 0 and bb_width < bb_width_median:
        # Confidence from how far below thresholds
        adx_conf = min(1.0, (ADX_RANGING_THRESHOLD - adx) / ADX_RANGING_THRESHOLD + 0.3)
        bb_conf = min(1.0, 1.0 - bb_width / bb_width_median + 0.3)
        confidence = (adx_conf + bb_conf) / 2
        return RegimeType.RANGING, confidence

    # 4. Ambiguous — between thresholds
    return RegimeType.UNKNOWN, 0.3
