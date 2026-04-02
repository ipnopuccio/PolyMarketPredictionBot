"""Abstract exchange adapter + shared data types for multi-exchange feeds."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizedTick:
    """Exchange-agnostic price tick."""
    exchange: str
    asset: str
    price: float
    volume: float          # in quote currency (USDT)
    bid: float = 0.0
    ask: float = 0.0
    timestamp: float = 0.0  # epoch seconds

    @property
    def spread(self) -> float:
        return self.ask - self.bid if self.ask and self.bid else 0.0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2 if self.bid and self.ask else self.price


@dataclass
class ExchangeHealth:
    """Health status for a single exchange connection."""
    exchange: str
    connected: bool = False
    latency_ms: float = 0.0
    last_update: float = 0.0
    error_count: int = 0
    last_error: str | None = None

    @property
    def is_healthy(self) -> bool:
        return self.connected and (time.time() - self.last_update < 30)

    @property
    def stale_seconds(self) -> float:
        return time.time() - self.last_update if self.last_update else float("inf")


class ExchangeAdapter(ABC):
    """Abstract base class for exchange feed adapters.

    Each adapter provides normalized price ticks for configured assets.
    The primary adapter (Binance) also provides full indicator data
    (CVD, funding, liquidations) via get_full_snapshot().
    Secondary adapters provide price/volume for validation and fallback.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Exchange identifier (e.g. 'binance', 'coinbase', 'kraken')."""

    @property
    @abstractmethod
    def is_primary(self) -> bool:
        """True if this adapter provides full indicator data (CVD, funding, etc.)."""

    @abstractmethod
    async def start(self) -> None:
        """Start the feed connection (WebSocket / polling)."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect."""

    @abstractmethod
    def get_tick(self, asset: str) -> NormalizedTick | None:
        """Latest normalized tick for an asset. None if no data yet."""

    @abstractmethod
    def get_health(self) -> ExchangeHealth:
        """Current connection health."""

    def get_full_snapshot(self, asset: str) -> dict[str, Any] | None:
        """Full indicator snapshot (only implemented by primary adapter).

        Returns dict compatible with FeedSnapshot fields, or None.
        """
        return None
