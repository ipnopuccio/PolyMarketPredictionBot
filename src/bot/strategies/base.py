"""Abstract base strategy for v2 bot."""
from __future__ import annotations

from abc import ABC, abstractmethod

from bot.config import StrategyConfig
from bot.core.types import FeedSnapshot, Signal, SignalResult


class BaseStrategy(ABC):
    """All strategies inherit from this and implement evaluate()."""

    name: str = "BASE"
    sizing_mode: str = "scaling"  # "scaling" | "dynamic"

    def __init__(self, cfg: StrategyConfig) -> None:
        self.cfg = cfg

    @property
    def signal_interval(self) -> int:
        return self.cfg.signal_interval

    @property
    def max_orders_per_window(self) -> int:
        return self.cfg.max_orders_per_window

    @property
    def max_elapsed_pct(self) -> float:
        return self.cfg.max_elapsed_pct

    @abstractmethod
    def evaluate(
        self,
        asset: str,
        snapshot: FeedSnapshot,
        rsi: float | None = None,
        bb: dict | None = None,
    ) -> SignalResult:
        """Evaluate the current state and return a signal result."""
        ...

    def entry_ok(self, asset: str, signal: Signal, entry_price: float) -> bool:
        """Validate entry price. Override in subclasses for custom guards."""
        return True

    def _result(
        self,
        signal: Signal,
        confidence: float,
        asset: str,
        snapshot: FeedSnapshot,
        indicators: dict | None = None,
    ) -> SignalResult:
        return SignalResult(
            signal=signal,
            confidence=min(1.0, max(0.0, confidence)),
            strategy=self.name,
            asset=asset,
            snapshot=snapshot,
            indicators=indicators or {},
        )
