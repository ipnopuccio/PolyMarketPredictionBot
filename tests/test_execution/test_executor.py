"""Tests for Executor -- pre-checks and trade placement."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import MomentumConfig, settings
from bot.core.events import EventBus
from bot.core.types import FeedSnapshot, MarketInfo, Signal, SignalResult
from bot.execution.executor import Executor
from bot.execution.risk import RiskManager
from bot.execution.sizer import Sizer
from bot.market.finder import MarketFinder
from bot.storage.database import Database
from tests.conftest import make_snapshot


STRATEGY = "MOMENTUM"
ASSET = "BTC"


# ---------------------------------------------------------------------------
# Fixtures local to this module
# ---------------------------------------------------------------------------

def _signal_result(
    signal: Signal = Signal.BUY_YES,
    strategy: str = STRATEGY,
    asset: str = ASSET,
    confidence: float = 0.8,
    snapshot: FeedSnapshot | None = None,
    indicators: dict | None = None,
) -> SignalResult:
    return SignalResult(
        signal=signal,
        confidence=confidence,
        strategy=strategy,
        asset=asset,
        snapshot=snapshot or make_snapshot(),
        indicators=indicators or {"regime": "TRENDING", "rsi": 45.0, "bb_pct": 0.5},
    )


def _market_info(
    asset: str = ASSET,
    market_id: str = "0xabc123",
    up_price: float = 0.52,
    down_price: float = 0.48,
) -> MarketInfo:
    return MarketInfo(
        asset=asset, market_id=market_id,
        event_title=f"{asset} Up/Down 5m",
        up_token_id="tok_up", down_token_id="tok_down",
        up_price=up_price, down_price=down_price,
        window_start=1_711_500_000, interval=300,
    )


def _executor(
    db: Database,
    event_bus: EventBus,
    *,
    find_market_return: MarketInfo | None = None,
    entry_price: float = 0.52,
    elapsed_pct: float = 0.30,
    kelly_size: float = 3.0,
    risk_ok: bool = True,
) -> Executor:
    market_finder = AsyncMock(spec=MarketFinder)
    market_finder.find_market = AsyncMock(
        return_value=find_market_return or _market_info(),
    )
    market_finder.get_entry_price = AsyncMock(return_value=entry_price)
    market_finder.window_elapsed_pct = MagicMock(return_value=elapsed_pct)

    sizer = AsyncMock(spec=Sizer)
    sizer.kelly_size = AsyncMock(return_value=kelly_size)

    risk = AsyncMock(spec=RiskManager)
    risk.check_all = AsyncMock(return_value=risk_ok)

    return Executor(db, market_finder, sizer, risk, event_bus)


# =========================================================================
# Signal.SKIP
# =========================================================================

class TestSkipSignal:
    async def test_skip_signal_returns_none(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        exe = _executor(db, event_bus)
        result = await exe.execute(
            _signal_result(signal=Signal.SKIP), MomentumConfig(),
        )
        assert result is None


# =========================================================================
# VPN guard
# =========================================================================

class TestVpnGuard:
    async def test_vpn_inactive_blocks(
        self, db: Database, event_bus: EventBus, mock_vpn_inactive,
    ):
        exe = _executor(db, event_bus)
        result = await exe.execute(
            _signal_result(), MomentumConfig(),
        )
        assert result is None

    async def test_vpn_active_allows(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        exe = _executor(db, event_bus, entry_price=0.52)
        result = await exe.execute(
            _signal_result(), MomentumConfig(),
        )
        assert result is not None


# =========================================================================
# Bankroll pre-check
# =========================================================================

class TestBankrollCheck:
    async def test_low_bankroll_blocks(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        """Bankroll < 1.0 => skip."""
        await db.conn.execute(
            "UPDATE bankroll SET current=0.50 WHERE strategy=? AND asset=?",
            (STRATEGY, ASSET),
        )
        await db.conn.commit()
        exe = _executor(db, event_bus)
        result = await exe.execute(
            _signal_result(), MomentumConfig(),
        )
        assert result is None


# =========================================================================
# Window capacity
# =========================================================================

class TestWindowCapacity:
    async def test_window_full_blocks(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        """After max_orders_per_window orders, further orders are blocked."""
        cfg = MomentumConfig(max_orders_per_window=1)
        exe = _executor(db, event_bus, entry_price=0.52)
        # First order succeeds
        r1 = await exe.execute(_signal_result(), cfg)
        assert r1 is not None
        # Second order should be blocked by window capacity
        r2 = await exe.execute(_signal_result(), cfg)
        assert r2 is None


# =========================================================================
# Elapsed % check
# =========================================================================

class TestElapsedPct:
    async def test_too_much_elapsed_blocks(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        exe = _executor(db, event_bus, elapsed_pct=0.95)
        cfg = MomentumConfig(max_elapsed_pct=0.70)
        result = await exe.execute(_signal_result(), cfg)
        assert result is None


# =========================================================================
# Risk check
# =========================================================================

class TestRiskCheck:
    async def test_risk_blocked(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        exe = _executor(db, event_bus, risk_ok=False)
        result = await exe.execute(_signal_result(), MomentumConfig())
        assert result is None


# =========================================================================
# Market availability
# =========================================================================

class TestMarketAvailability:
    async def test_no_market_found_blocks(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        exe = _executor(db, event_bus, find_market_return=None)
        exe._mf.find_market = AsyncMock(return_value=None)
        result = await exe.execute(_signal_result(), MomentumConfig())
        assert result is None


# =========================================================================
# Entry price validation
# =========================================================================

class TestEntryPrice:
    async def test_entry_at_or_above_one_blocks(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        """After slippage, if entry >= 1.0, should block."""
        # With slippage_bps=50, entry_price * (1 + 0.005) needs to be >= 1.0
        exe = _executor(db, event_bus, entry_price=1.0)
        result = await exe.execute(_signal_result(), MomentumConfig())
        assert result is None

    async def test_entry_at_zero_blocks(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        """Entry price 0 is invalid."""
        exe = _executor(db, event_bus, entry_price=0.0)
        result = await exe.execute(_signal_result(), MomentumConfig())
        assert result is None

    async def test_entry_exceeds_max_entry_guard_blocks(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        """Strategy max_entry_buy_yes = 0.55, entry after slippage = 0.56 => blocked."""
        cfg = MomentumConfig(max_entry_buy_yes=0.55)
        # entry = 0.56, after slippage (50bps) = 0.56 * 1.005 ~ 0.5628
        exe = _executor(db, event_bus, entry_price=0.56)
        result = await exe.execute(
            _signal_result(signal=Signal.BUY_YES), cfg,
        )
        assert result is None


# =========================================================================
# Idempotency (dynamic mode)
# =========================================================================

class TestIdempotency:
    async def test_dynamic_mode_blocks_duplicate_market(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        """In dynamic mode (scaling=False), one order per market per window."""
        exe = _executor(db, event_bus, entry_price=0.52)
        cfg = MomentumConfig(max_orders_per_window=10)
        # First dynamic order
        r1 = await exe.execute(_signal_result(), cfg, scaling=False)
        assert r1 is not None
        # Same market again => blocked
        r2 = await exe.execute(_signal_result(), cfg, scaling=False)
        assert r2 is None


# =========================================================================
# Successful trade
# =========================================================================

class TestSuccessfulTrade:
    async def test_successful_trade_returns_id_and_publishes(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        events: list[dict] = []

        async def capture(data: dict) -> None:
            events.append(data)

        event_bus.subscribe("trade.placed", capture)

        exe = _executor(db, event_bus, entry_price=0.52)
        trade_id = await exe.execute(_signal_result(), MomentumConfig())
        assert trade_id is not None
        assert isinstance(trade_id, int)

        # Event was published
        assert len(events) == 1
        assert events[0]["trade_id"] == trade_id
        assert events[0]["signal"] == "BUY_YES"

    async def test_gas_fee_deducted(
        self, db: Database, event_bus: EventBus, mock_vpn_active,
    ):
        bankroll_before = await db.get_bankroll(STRATEGY, ASSET)
        exe = _executor(db, event_bus, entry_price=0.52, kelly_size=3.0)
        trade_id = await exe.execute(_signal_result(), MomentumConfig())
        assert trade_id is not None

        bankroll_after = await db.get_bankroll(STRATEGY, ASSET)
        # Bankroll should be reduced by (entry * bet_size) + gas
        entry_after_slippage = 0.52 * (1 + settings.fees.slippage_bps / 10000)
        usdc_cost = entry_after_slippage * 3.0
        expected = bankroll_before - usdc_cost - settings.fees.gas_per_trade
        assert abs(bankroll_after - expected) < 0.01
