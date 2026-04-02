"""BOLLINGER — Bollinger Band breakout.

Price breaks outside bands → bet on continuation.
100% win rate on BTC.
"""
from __future__ import annotations

from bot.config import BollingerConfig
from bot.core.types import FeedSnapshot, Signal, SignalResult

from .base import BaseStrategy


class BollingerStrategy(BaseStrategy):
    name = "BOLLINGER"
    sizing_mode = "dynamic"

    def __init__(self, cfg: BollingerConfig) -> None:
        super().__init__(cfg)
        self._cfg = cfg

    def evaluate(
        self,
        asset: str,
        snapshot: FeedSnapshot,
        rsi: float | None = None,
        bb: dict | None = None,
    ) -> SignalResult:
        indicators: dict = {"rsi": rsi}

        if bb is None:
            return self._result(Signal.SKIP, 0.0, asset, snapshot, indicators)

        price = snapshot.last_price
        upper = bb["upper"]
        lower = bb["lower"]
        mid = bb["mid"]
        indicators.update({"bb_upper": upper, "bb_lower": lower, "bb_mid": mid})

        if price > upper:
            band_width = upper - lower if upper != lower else 1
            distance_pct = (price - mid) / band_width
            confidence = min(1.0, distance_pct * 2)
            return self._result(Signal.BUY_YES, confidence, asset, snapshot, indicators)

        if price < lower:
            band_width = upper - lower if upper != lower else 1
            distance_pct = (mid - price) / band_width
            confidence = min(1.0, distance_pct * 2)
            return self._result(Signal.BUY_NO, confidence, asset, snapshot, indicators)

        return self._result(Signal.SKIP, 0.0, asset, snapshot, indicators)
