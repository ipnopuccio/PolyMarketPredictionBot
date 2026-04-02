"""
RSI-14 and Bollinger Band calculator from 1-min closes (v2).

Accepts price updates, buckets them into 1-minute candles,
then computes RSI-14 and Bollinger Bands (20-period, 2-std)
over a rolling 30-candle history per asset.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

ASSETS = ("BTC", "ETH", "SOL")
MAX_CANDLES = 30


class RSIFeed:
    """Self-contained RSI and Bollinger calculator."""

    def __init__(self) -> None:
        self._closes: dict[str, deque[float]] = {
            a: deque(maxlen=MAX_CANDLES) for a in ASSETS
        }
        self._current_candle: dict[str, dict[str, Any]] = {
            a: {"open_time": 0, "prices": []} for a in ASSETS
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, asset: str, price: float) -> None:
        """Feed a new price tick. Closes the candle on minute boundary."""
        if price <= 0:
            return

        now_min = self._current_minute_ts()
        candle = self._current_candle[asset]

        if candle["open_time"] == 0:
            candle["open_time"] = now_min
            candle["prices"] = [price]
        elif now_min == candle["open_time"]:
            candle["prices"].append(price)
        else:
            # Minute boundary crossed: close the current candle
            if candle["prices"]:
                self._closes[asset].append(candle["prices"][-1])
            candle["open_time"] = now_min
            candle["prices"] = [price]

    def get_rsi(self, asset: str, period: int = 14) -> float | None:
        """RSI-14 from closed 1-min candles. Returns None if insufficient data."""
        closes = list(self._closes[asset])
        if len(closes) < period + 1:
            return None

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = deltas[-period:]
        gains = [max(d, 0) for d in recent]
        losses = [abs(min(d, 0)) for d in recent]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    def get_bollinger(
        self, asset: str, period: int = 20, std_mult: float = 2.0
    ) -> dict[str, float] | None:
        """Bollinger Bands. Returns {upper, mid, lower, pct} or None."""
        closes = list(self._closes[asset])
        if len(closes) < period:
            return None

        window = closes[-period:]
        mean = sum(window) / period
        variance = sum((p - mean) ** 2 for p in window) / period
        std = variance ** 0.5

        upper = round(mean + std_mult * std, 2)
        lower = round(mean - std_mult * std, 2)
        mid = round(mean, 2)

        # Bollinger %B: where price sits relative to the bands (0 = lower, 1 = upper)
        price = closes[-1]
        band_width = upper - lower
        pct = round((price - lower) / band_width, 4) if band_width > 0 else 0.5

        return {"upper": upper, "mid": mid, "lower": lower, "pct": pct}

    @property
    def candle_counts(self) -> dict[str, int]:
        """Number of closed candles per asset (for diagnostics)."""
        return {a: len(self._closes[a]) for a in ASSETS}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _current_minute_ts() -> int:
        return int(time.time() // 60) * 60
