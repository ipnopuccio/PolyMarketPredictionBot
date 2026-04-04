"""
Multi-timeframe trend tracker (Phase 12.2).

Builds OHLC candles from price ticks at two resolutions:
  - 1-minute candles -> 5-minute EMA trend
  - 5-minute candles -> 15-minute EMA trend

Trend direction (UP / DOWN / FLAT) is derived from the slope of
EMA-5 over the candle closes.  A signal is confirmed when the
short timeframe (5m) trend does not contradict the signal direction.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

# -- Constants ----------------------------------------------------------------

MAX_CANDLES = 20          # rolling buffer per timeframe per asset
EMA_PERIOD = 5            # EMA length for slope calculation
FLAT_THRESHOLD = 1e-8     # absolute slope below this is FLAT

# Candle durations in seconds
_CANDLE_SECS = {"1m": 60, "5m": 300}


# -- Helpers ------------------------------------------------------------------

def _ema(values: list[float], period: int) -> list[float]:
    """Compute EMA series over *values* with given *period*.

    Returns a list the same length as *values*.  The first element
    equals the first value; subsequent elements use the standard
    exponential smoothing formula.
    """
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _bucket_ts(ts: float, seconds: int) -> int:
    """Floor *ts* to the nearest candle boundary."""
    return int(ts // seconds) * seconds


# -- OHLC Candle helper -------------------------------------------------------

class _CandleBuilder:
    """Accumulates ticks into OHLC candles of fixed duration."""

    def __init__(self, duration_secs: int, maxlen: int = MAX_CANDLES) -> None:
        self._duration = duration_secs
        self._closes: deque[float] = deque(maxlen=maxlen)
        # Current (incomplete) candle state
        self._cur_bucket: int = 0
        self._cur_prices: list[float] = []

    @property
    def closes(self) -> list[float]:
        return list(self._closes)

    @property
    def count(self) -> int:
        return len(self._closes)

    def update(self, price: float, ts: float) -> None:
        """Feed a new tick.  Closes the candle when the bucket rolls over."""
        bucket = _bucket_ts(ts, self._duration)

        if self._cur_bucket == 0:
            # First tick ever
            self._cur_bucket = bucket
            self._cur_prices = [price]
            return

        if bucket == self._cur_bucket:
            self._cur_prices.append(price)
        else:
            # Bucket changed -> close previous candle
            if self._cur_prices:
                self._closes.append(self._cur_prices[-1])
            self._cur_bucket = bucket
            self._cur_prices = [price]


# -- Public class --------------------------------------------------------------

class MultiTimeframeTrend:
    """Tracks price trends across 5-minute and 15-minute timeframes.

    Usage::

        mtt = MultiTimeframeTrend()

        # Feed every price tick
        mtt.update("BTC", price, time.time())

        # Query trend
        mtt.get_trend("BTC", "5m")   # -> "UP" | "DOWN" | "FLAT"

        # Confirm a signal
        mtt.is_confirmed("BTC", "BUY_YES")  # True if 5m trend != DOWN
    """

    def __init__(self) -> None:
        # Per-asset candle builders:
        #   "1m" builder -> feeds the 5m trend (EMA on 1-min closes)
        #   "5m" builder -> feeds the 15m trend (EMA on 5-min closes)
        self._builders: dict[str, dict[str, _CandleBuilder]] = {}

    # -- internal helpers -----------------------------------------------------

    def _ensure_asset(self, asset: str) -> None:
        if asset not in self._builders:
            self._builders[asset] = {
                "1m": _CandleBuilder(_CANDLE_SECS["1m"]),
                "5m": _CandleBuilder(_CANDLE_SECS["5m"]),
            }

    @staticmethod
    def _compute_trend(closes: list[float]) -> str:
        """Derive trend direction from EMA-5 slope on candle closes."""
        if len(closes) < EMA_PERIOD:
            return "FLAT"
        ema_vals = _ema(closes, EMA_PERIOD)
        # Slope = last two EMA values
        slope = ema_vals[-1] - ema_vals[-2]
        if abs(slope) < FLAT_THRESHOLD:
            return "FLAT"
        return "UP" if slope > 0 else "DOWN"

    # -- Public API -----------------------------------------------------------

    def update(self, asset: str, price: float, timestamp: float) -> None:
        """Record a new price tick.  Internally builds 1-min and 5-min candles."""
        if price <= 0:
            return
        self._ensure_asset(asset)
        self._builders[asset]["1m"].update(price, timestamp)
        self._builders[asset]["5m"].update(price, timestamp)

    def get_trend(self, asset: str, timeframe: str = "5m") -> str:
        """Return trend direction: ``'UP'``, ``'DOWN'``, or ``'FLAT'``.

        *timeframe*: ``'5m'`` (uses 1-min candles) or ``'15m'`` (uses 5-min candles).
        """
        self._ensure_asset(asset)
        if timeframe == "5m":
            closes = self._builders[asset]["1m"].closes
        elif timeframe == "15m":
            closes = self._builders[asset]["5m"].closes
        else:
            return "FLAT"
        return self._compute_trend(closes)

    def is_confirmed(self, asset: str, signal_direction: str) -> bool:
        """Check if *signal_direction* aligns with higher-timeframe trend.

        *signal_direction*: ``'BUY_YES'`` (expecting UP) or ``'BUY_NO'``
        (expecting DOWN).

        Returns ``True`` if:
        - 5-min trend agrees with signal direction, **or**
        - Not enough data yet (don't block during warmup).
        """
        self._ensure_asset(asset)
        # During warmup, always confirm
        if self._builders[asset]["1m"].count < EMA_PERIOD:
            return True

        trend_5m = self.get_trend(asset, "5m")

        if signal_direction == "BUY_YES":
            return trend_5m != "DOWN"
        elif signal_direction == "BUY_NO":
            return trend_5m != "UP"
        # Unknown direction -> don't block
        return True

    def get_all_trends(self, asset: str) -> dict[str, str]:
        """Return current trend state for dashboard display."""
        return {
            "5m": self.get_trend(asset, "5m"),
            "15m": self.get_trend(asset, "15m"),
        }
