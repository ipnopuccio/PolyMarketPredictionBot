"""MOMENTUM — CVD + VWAP trend following.

Strong order flow + price trend in the same direction.
96-100% win rate on BTC and SOL.
"""
from __future__ import annotations

from bot.config import MomentumConfig
from bot.core.types import FeedSnapshot, Signal, SignalResult

from .base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    name = "MOMENTUM"
    sizing_mode = "scaling"

    def __init__(self, cfg: MomentumConfig) -> None:
        super().__init__(cfg)
        self._cfg = cfg

    def evaluate(
        self,
        asset: str,
        snapshot: FeedSnapshot,
        rsi: float | None = None,
        bb: dict | None = None,
    ) -> SignalResult:
        cvd = snapshot.cvd_2min
        vwap = snapshot.vwap_change
        thresh = self._cfg.cvd_threshold
        vwap_thresh = self._cfg.vwap_threshold

        indicators = {"cvd": cvd, "vwap_change": vwap}

        if cvd > thresh and vwap > vwap_thresh:
            confidence = min(1.0, abs(cvd) / (thresh * 3))
            return self._result(Signal.BUY_YES, confidence, asset, snapshot, indicators)

        if cvd < -thresh and vwap < -vwap_thresh:
            confidence = min(1.0, abs(cvd) / (thresh * 3))
            return self._result(Signal.BUY_NO, confidence, asset, snapshot, indicators)

        return self._result(Signal.SKIP, 0.0, asset, snapshot, indicators)

    def entry_ok(self, asset: str, signal: Signal, entry_price: float) -> bool:
        """Reject entries above max price thresholds (ported from v1)."""
        if signal == Signal.BUY_YES:
            return entry_price <= self._cfg.max_entry_buy_yes
        if signal == Signal.BUY_NO:
            return entry_price <= self._cfg.max_entry_buy_no
        return False
