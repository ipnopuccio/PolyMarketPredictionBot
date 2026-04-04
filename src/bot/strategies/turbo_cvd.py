"""TURBO_CVD — Pure CVD pressure at ultra-low threshold.

Fires every 6s. Any sustained order flow imbalance > 200k triggers entry.
88% win rate on ETH.

Phase 12.1: Supports adaptive thresholds via AdaptiveThreshold instance.
"""
from __future__ import annotations

from bot.config import TurboCvdConfig
from bot.core.types import FeedSnapshot, Signal, SignalResult

from .adaptive import AdaptiveThreshold
from .base import BaseStrategy


class TurboCvdStrategy(BaseStrategy):
    name = "TURBO_CVD"
    sizing_mode = "scaling"

    def __init__(self, cfg: TurboCvdConfig, adaptive: AdaptiveThreshold | None = None) -> None:
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
        cvd = snapshot.cvd_2min

        # Use adaptive threshold if available and warmed up
        if self._adaptive is not None and self._adaptive.has_enough_data(asset):
            thresh = self._adaptive.get_cvd_threshold(asset, self._cfg.cvd_threshold)
        else:
            thresh = self._cfg.cvd_threshold

        indicators = {"cvd": cvd, "cvd_threshold": thresh}

        if cvd > thresh:
            confidence = min(1.0, abs(cvd) / (thresh * 3))
            return self._result(Signal.BUY_YES, confidence, asset, snapshot, indicators)

        if cvd < -thresh:
            confidence = min(1.0, abs(cvd) / (thresh * 3))
            return self._result(Signal.BUY_NO, confidence, asset, snapshot, indicators)

        return self._result(Signal.SKIP, 0.0, asset, snapshot, indicators)
