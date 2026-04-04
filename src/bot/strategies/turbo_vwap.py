"""TURBO_VWAP — Pure VWAP deviation.

Price deviating > 0.02% from VWAP signals short-term trend continuation.
Fires every 6s. 83.8% win rate on ETH.

Phase 12.1: Supports adaptive thresholds via AdaptiveThreshold instance.
"""
from __future__ import annotations

from bot.config import TurboVwapConfig
from bot.core.types import FeedSnapshot, Signal, SignalResult

from .adaptive import AdaptiveThreshold
from .base import BaseStrategy


class TurboVwapStrategy(BaseStrategy):
    name = "TURBO_VWAP"
    sizing_mode = "scaling"

    def __init__(self, cfg: TurboVwapConfig, adaptive: AdaptiveThreshold | None = None) -> None:
        super().__init__(cfg)
        self._cfg = cfg
        self._adaptive = adaptive

    def evaluate(
        self,
        asset: str,
        snapshot: FeedSnapshot,
        rsi: float | None = None,
        bb: dict | None = None,
    ) -> SignalResult:
        vwap = snapshot.vwap_change

        # Use adaptive threshold if available and warmed up
        if self._adaptive is not None and self._adaptive.has_enough_data(asset):
            thresh = self._adaptive.get_vwap_threshold(asset, self._cfg.vwap_threshold)
        else:
            thresh = self._cfg.vwap_threshold

        indicators = {"vwap_change": vwap, "vwap_threshold": thresh}

        if vwap > thresh:
            confidence = min(1.0, abs(vwap) / (thresh * 3))
            return self._result(Signal.BUY_YES, confidence, asset, snapshot, indicators)

        if vwap < -thresh:
            confidence = min(1.0, abs(vwap) / (thresh * 3))
            return self._result(Signal.BUY_NO, confidence, asset, snapshot, indicators)

        return self._result(Signal.SKIP, 0.0, asset, snapshot, indicators)
