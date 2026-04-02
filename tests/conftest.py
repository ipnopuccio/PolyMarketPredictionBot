"""Shared fixtures for btc-bot-v2 test suite."""
from __future__ import annotations

import time
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import (
    BollingerConfig,
    MomentumConfig,
    SizerConfig,
    RiskConfig,
    TurboCvdConfig,
    TurboVwapConfig,
)
from bot.core.events import EventBus
from bot.core.types import FeedSnapshot, MarketInfo, Signal
from bot.storage.database import Database


# ---------------------------------------------------------------------------
# Feed snapshot: defaults trigger BUY_YES on ALL strategies
# Use dataclasses.replace() to create variants (frozen dataclass).
# ---------------------------------------------------------------------------

@pytest.fixture
def feed_snapshot() -> FeedSnapshot:
    """Realistic BTC snapshot — above all strategy thresholds for BUY_YES."""
    return FeedSnapshot(
        last_price=67_500.0,
        price_2min_ago=67_450.0,
        vwap_change=0.00074,       # > Momentum 0.0005, > TurboVwap 0.0002
        cvd_2min=1_500_000.0,      # > Momentum 1M, > TurboCvd 200K
        funding_rate=0.0001,
        liq_long_2min=50_000.0,
        liq_short_2min=30_000.0,
        bid=67_499.0,
        ask=67_501.0,
        book_imbalance=0.15,
        open_interest=5_000_000_000.0,
        long_short_ratio=1.05,
        connected=True,
        last_update=time.time(),
    )


# ---------------------------------------------------------------------------
# Database: fresh async SQLite in tmp_path with seeded bankroll
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path) -> Database:
    """Fresh test database with schema + bankroll seeded at $40 per bot."""
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    await database.connect()
    await database.seed_bankroll(
        ["MOMENTUM", "BOLLINGER", "TURBO_CVD", "TURBO_VWAP"],
        ["BTC", "ETH", "SOL"],
        40.0,
    )
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# Strategy configs (use default values from config.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def momentum_config() -> MomentumConfig:
    return MomentumConfig()


@pytest.fixture
def bollinger_config() -> BollingerConfig:
    return BollingerConfig()


@pytest.fixture
def turbo_cvd_config() -> TurboCvdConfig:
    return TurboCvdConfig()


@pytest.fixture
def turbo_vwap_config() -> TurboVwapConfig:
    return TurboVwapConfig()


@pytest.fixture
def sizer_config() -> SizerConfig:
    return SizerConfig()


@pytest.fixture
def risk_config() -> RiskConfig:
    return RiskConfig()


# ---------------------------------------------------------------------------
# Market info
# ---------------------------------------------------------------------------

@pytest.fixture
def market_info() -> MarketInfo:
    return MarketInfo(
        asset="BTC",
        market_id="0xabc123",
        event_title="BTC Up/Down 5m 1711500000",
        up_token_id="tok_up_001",
        down_token_id="tok_down_001",
        up_price=0.52,
        down_price=0.48,
        window_start=1_711_500_000,
        interval=300,
    )


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------

@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


# ---------------------------------------------------------------------------
# VPN mock — always active in tests
# Patch at the import location in executor.py
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_vpn_active():
    with patch(
        "bot.execution.executor.is_vpn_active",
        new_callable=AsyncMock,
        return_value=True,
    ):
        yield


@pytest.fixture
def mock_vpn_inactive():
    with patch(
        "bot.execution.executor.is_vpn_active",
        new_callable=AsyncMock,
        return_value=False,
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers (not fixtures — import in test files)
# ---------------------------------------------------------------------------

def make_snapshot(**overrides) -> FeedSnapshot:
    """Create a FeedSnapshot with custom fields. Non-specified fields are 0."""
    defaults = {
        "last_price": 67_500.0,
        "price_2min_ago": 67_450.0,
        "connected": True,
        "last_update": time.time(),
    }
    defaults.update(overrides)
    return FeedSnapshot(**defaults)


async def insert_resolved_trades(
    db: Database,
    strategy: str,
    asset: str,
    wins: int,
    losses: int,
    entry_price: float = 0.45,
    bet_size: float = 1.0,
) -> None:
    """Insert resolved trades into the DB for stats testing."""
    snapshot = FeedSnapshot(connected=True, last_update=time.time())
    for _ in range(wins):
        tid = await db.reserve_and_insert_trade(
            strategy=strategy,
            asset=asset,
            market_id="test_mkt",
            signal="BUY_YES",
            entry_price=entry_price,
            bet_size=bet_size,
            confidence=0.8,
            regime="UNKNOWN",
            snapshot=snapshot.to_dict(),
            rsi=None,
            bb_pct=None,
        )
        if tid is not None:
            pnl = (1 - entry_price) * bet_size * (1 - 0.02)
            await db.resolve_trade(tid, "WIN", pnl)
    for _ in range(losses):
        tid = await db.reserve_and_insert_trade(
            strategy=strategy,
            asset=asset,
            market_id="test_mkt",
            signal="BUY_YES",
            entry_price=entry_price,
            bet_size=bet_size,
            confidence=0.8,
            regime="UNKNOWN",
            snapshot=snapshot.to_dict(),
            rsi=None,
            bb_pct=None,
        )
        if tid is not None:
            pnl = -entry_price * bet_size
            await db.resolve_trade(tid, "LOSS", pnl)
