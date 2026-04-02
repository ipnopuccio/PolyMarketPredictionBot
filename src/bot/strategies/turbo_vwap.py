"""TURBO_VWAP — Pure VWAP deviation.

Price deviating > 0.02% from VWAP signals short-term trend continuation.
Fires every 6s. 83.8% win rate on ETH.
"""
from __future__ import annotations

from bot.config import TurboVwapConfig
from bot.core.types import FeedSnapshot, Signal, SignalResult

from .base import BaseStrategy


class TurboVwapStrategy(BaseStrategy):
    name = "TURBO_VWAP"
    sizing_mode = "scaling"

    def __init__(self, cfg: TurboVwapConfig) -> None:
        super().__init__(cfg)
        self._cfg = cfg

    def evaluate(
        self,
        asset: str,
        snapshot: FeedSnapshot,
        rsi: float | None = None,
        bb: dict | None = None,
    ) -> SignalResult:
        vwap = snapshot.vwap_change
        thresh = self._cfg.vwap_threshold
        indicators = {"vwap_change": vwap}

        if vwap > thresh:
            confidence = min(1.0, abs(vwap) / (thresh * 3))
            return self._result(Signal.BUY_YES, confidence, asset, snapshot, indicators)

        if vwap < -thresh:
            confidence = min(1.0, abs(vwap) / (thresh * 3))
            return self._result(Signal.BUY_NO, confidence, asset, snapshot, indicators)

        return self._result(Signal.SKIP, 0.0, asset, snapshot, indicators)
