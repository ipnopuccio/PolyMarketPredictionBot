"""Core types used across the entire bot."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Signal(str, Enum):
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    SKIP = "SKIP"


class Regime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    UNKNOWN = "UNKNOWN"


class RegimeType(str, Enum):
    """Market regime classification (used by regime detector)."""
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeResult:
    """Output of the regime classifier."""
    regime: RegimeType
    adx: float
    bb_width: float
    ema_slope: float
    confidence: float  # 0.0 to 1.0


@dataclass(frozen=True)
class FeedSnapshot:
    """Immutable snapshot of Binance feed state for one asset."""
    last_price: float = 0.0
    price_2min_ago: float = 0.0
    vwap_change: float = 0.0
    cvd_2min: float = 0.0
    funding_rate: float = 0.0
    liq_long_2min: float = 0.0
    liq_short_2min: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    book_imbalance: float = 0.0
    open_interest: float = 0.0
    long_short_ratio: float = 0.0
    connected: bool = False
    last_update: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_price": self.last_price,
            "price_2min_ago": self.price_2min_ago,
            "vwap_change": self.vwap_change,
            "cvd_2min": self.cvd_2min,
            "funding_rate": self.funding_rate,
            "liq_long_2min": self.liq_long_2min,
            "liq_short_2min": self.liq_short_2min,
            "bid": self.bid,
            "ask": self.ask,
            "book_imbalance": self.book_imbalance,
            "open_interest": self.open_interest,
            "long_short_ratio": self.long_short_ratio,
        }


@dataclass(frozen=True)
class SignalResult:
    """Output of a strategy evaluation."""
    signal: Signal
    confidence: float  # 0.0 to 1.0
    strategy: str
    asset: str
    snapshot: FeedSnapshot
    indicators: dict[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketInfo:
    """Polymarket Up/Down market metadata."""
    asset: str
    market_id: str
    event_title: str
    up_token_id: str | None
    down_token_id: str | None
    up_price: float
    down_price: float
    window_start: int
    interval: int


# ── Active bots (single source of truth) ────────────────────────────────────
# Only the 5 strategy/asset pairs validated profitable in v1.
# Every module that needs this list should import it from here.
ACTIVE_BOTS: list[tuple[str, str]] = [
    ("TURBO_CVD", "ETH"),
    ("TURBO_VWAP", "ETH"),
    ("MOMENTUM", "BTC"),
    ("MOMENTUM", "SOL"),
    ("BOLLINGER", "BTC"),
]


@dataclass
class TradeRecord:
    """A single trade in the database."""
    id: int
    timestamp: str
    strategy: str
    asset: str
    market_id: str | None
    signal: str
    entry_price: float
    bet_size: float
    confidence: float
    regime: str
    outcome: str | None = None
    pnl: float | None = None
    cvd_at_signal: float | None = None
    funding_at_signal: float | None = None
    liq_long_at_signal: float | None = None
    liq_short_at_signal: float | None = None
    vwap_change_at_signal: float | None = None
    rsi_at_signal: float | None = None
    bb_pct_at_signal: float | None = None
