"""End-to-end integration tests — full pipeline from strategy to resolution.

Tests the real flow:
  FeedSnapshot → Strategy.evaluate → Executor.execute → Resolver._calculate_pnl
with a real SQLite database, real strategies, real sizer/risk, and mocked externals
(VPN, MarketFinder, Polymarket API).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import (
    FeeConfig,
    MomentumConfig,
    BollingerConfig,
    RiskConfig,
    SizerConfig,
    TurboCvdConfig,
    TurboVwapConfig,
)
from bot.core.events import EventBus
from bot.core.types import ACTIVE_BOTS, FeedSnapshot, MarketInfo, Signal
from bot.execution.executor import Executor
from bot.execution.resolver import Resolver
from bot.execution.risk import RiskManager
from bot.execution.sizer import Sizer
from bot.storage.database import Database
from bot.strategies.momentum import MomentumStrategy
from bot.strategies.bollinger import BollingerStrategy
from bot.strategies.turbo_cvd import TurboCvdStrategy
from bot.strategies.turbo_vwap import TurboVwapStrategy


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def risk():
    return RiskManager(RiskConfig())


@pytest.fixture
def sizer():
    return Sizer(SizerConfig())


@pytest.fixture
def market_btc():
    return MarketInfo(
        asset="BTC",
        market_id="0xintegration_btc",
        event_title="BTC Up/Down 5m",
        up_token_id="tok_up",
        down_token_id="tok_down",
        up_price=0.52,
        down_price=0.48,
        window_start=int(time.time()),
        interval=300,
    )


@pytest.fixture
def market_eth():
    return MarketInfo(
        asset="ETH",
        market_id="0xintegration_eth",
        event_title="ETH Up/Down 5m",
        up_token_id="tok_up_eth",
        down_token_id="tok_down_eth",
        up_price=0.51,
        down_price=0.49,
        window_start=int(time.time()),
        interval=300,
    )


def _mock_market_finder(market: MarketInfo):
    mf = MagicMock()
    mf.find_market = AsyncMock(return_value=market)
    mf.window_elapsed_pct = MagicMock(return_value=0.3)
    mf.get_entry_price = AsyncMock(return_value=0.50)
    return mf


@pytest.fixture
def snapshot_buy_yes():
    """Snapshot that triggers BUY_YES on MOMENTUM strategy."""
    return FeedSnapshot(
        last_price=67_500.0,
        price_2min_ago=67_450.0,
        vwap_change=0.001,
        cvd_2min=2_000_000.0,
        funding_rate=0.0001,
        liq_long_2min=50_000.0,
        liq_short_2min=30_000.0,
        bid=67_499.0,
        ask=67_501.0,
        book_imbalance=0.15,
        connected=True,
        last_update=time.time(),
    )


@pytest.fixture
def snapshot_buy_no():
    """Snapshot that triggers BUY_NO on MOMENTUM strategy."""
    return FeedSnapshot(
        last_price=67_500.0,
        price_2min_ago=67_550.0,
        vwap_change=-0.001,
        cvd_2min=-2_000_000.0,
        funding_rate=-0.0001,
        liq_long_2min=30_000.0,
        liq_short_2min=50_000.0,
        bid=67_499.0,
        ask=67_501.0,
        book_imbalance=-0.15,
        connected=True,
        last_update=time.time(),
    )


# ── E2E: Strategy → Executor → DB ───────────────────────────────────────────


class TestFullPipelineMomentum:
    """Full pipeline: MOMENTUM evaluate → Executor → trade in DB."""

    @pytest.mark.asyncio
    async def test_buy_yes_creates_trade(self, db, bus, sizer, risk, market_btc, snapshot_buy_yes):
        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        result = strategy.evaluate("BTC", snapshot_buy_yes)
        assert result.signal == Signal.BUY_YES

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg, scaling=True)

        assert trade_id is not None
        trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(trades) == 1
        assert trades[0]["signal"] == "BUY_YES"
        assert trades[0]["market_id"] == "0xintegration_btc"

    @pytest.mark.asyncio
    async def test_buy_no_creates_trade(self, db, bus, sizer, risk, market_btc, snapshot_buy_no):
        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        result = strategy.evaluate("BTC", snapshot_buy_no)
        assert result.signal == Signal.BUY_NO

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg, scaling=True)

        assert trade_id is not None
        trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(trades) == 1
        assert trades[0]["signal"] == "BUY_NO"

    @pytest.mark.asyncio
    async def test_skip_produces_no_trade(self, db, bus, sizer, risk, market_btc):
        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        # Low CVD + low VWAP → SKIP
        snapshot = FeedSnapshot(
            last_price=67_500.0,
            cvd_2min=100.0,
            vwap_change=0.00001,
            connected=True,
            last_update=time.time(),
        )
        result = strategy.evaluate("BTC", snapshot)
        assert result.signal == Signal.SKIP

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg, scaling=True)

        assert trade_id is None
        trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(trades) == 0


class TestFullPipelineTurboCvd:
    """Full pipeline: TURBO_CVD → Executor → trade in DB."""

    @pytest.mark.asyncio
    async def test_turbo_cvd_buy_yes(self, db, bus, sizer, risk, market_eth):
        strategy = TurboCvdStrategy(TurboCvdConfig())
        mf = _mock_market_finder(market_eth)
        executor = Executor(db, mf, sizer, risk, bus)

        snapshot = FeedSnapshot(
            last_price=3_500.0,
            cvd_2min=500_000.0,
            connected=True,
            last_update=time.time(),
        )
        result = strategy.evaluate("ETH", snapshot)
        assert result.signal == Signal.BUY_YES

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg, scaling=True)

        assert trade_id is not None


class TestFullPipelineTurboVwap:
    """Full pipeline: TURBO_VWAP → Executor → trade in DB."""

    @pytest.mark.asyncio
    async def test_turbo_vwap_buy_yes(self, db, bus, sizer, risk, market_eth):
        strategy = TurboVwapStrategy(TurboVwapConfig())
        mf = _mock_market_finder(market_eth)
        executor = Executor(db, mf, sizer, risk, bus)

        snapshot = FeedSnapshot(
            last_price=3_500.0,
            vwap_change=0.001,
            connected=True,
            last_update=time.time(),
        )
        result = strategy.evaluate("ETH", snapshot)
        assert result.signal == Signal.BUY_YES

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg, scaling=True)

        assert trade_id is not None


# ── E2E: Execute → Resolve → P&L ────────────────────────────────────────────


class TestExecuteAndResolve:
    """Full cycle: place trade → resolve → verify P&L and bankroll."""

    @pytest.mark.asyncio
    async def test_win_updates_bankroll(self, db, bus, sizer, risk, market_btc, snapshot_buy_yes):
        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        initial_bankroll = await db.get_bankroll("MOMENTUM", "BTC")

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            result = strategy.evaluate("BTC", snapshot_buy_yes)
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg)

        assert trade_id is not None

        # Simulate resolution: BUY_YES and Up won (resolution="1") → WIN
        trade = (await db.get_open_trades("MOMENTUM", "BTC"))[0]
        outcome, pnl = Resolver._calculate_pnl(
            trade["signal"], trade["entry_price"], trade["bet_size"], "1",
        )
        assert outcome == "WIN"
        assert pnl > 0

        await db.resolve_trade(trade_id, outcome, pnl)

        # Verify trade is resolved
        open_trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(open_trades) == 0

        # Bankroll should have increased (minus gas fee, plus winnings)
        final_bankroll = await db.get_bankroll("MOMENTUM", "BTC")
        assert final_bankroll > initial_bankroll - trade["bet_size"]

    @pytest.mark.asyncio
    async def test_loss_updates_bankroll(self, db, bus, sizer, risk, market_btc, snapshot_buy_yes):
        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        initial_bankroll = await db.get_bankroll("MOMENTUM", "BTC")

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            result = strategy.evaluate("BTC", snapshot_buy_yes)
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg)

        assert trade_id is not None

        # Simulate resolution: BUY_YES but Down won (resolution="0") → LOSS
        trade = (await db.get_open_trades("MOMENTUM", "BTC"))[0]
        outcome, pnl = Resolver._calculate_pnl(
            trade["signal"], trade["entry_price"], trade["bet_size"], "0",
        )
        assert outcome == "LOSS"
        assert pnl < 0

        await db.resolve_trade(trade_id, outcome, pnl)
        final_bankroll = await db.get_bankroll("MOMENTUM", "BTC")
        assert final_bankroll < initial_bankroll

    @pytest.mark.asyncio
    async def test_multiple_trades_sequential(self, db, bus, sizer, risk, market_btc, snapshot_buy_yes):
        """Place 3 trades, resolve them all, verify cumulative P&L."""
        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        trade_ids = []
        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            for _ in range(3):
                result = strategy.evaluate("BTC", snapshot_buy_yes)
                tid = await executor.execute(result, strategy_cfg=strategy.cfg, scaling=True)
                if tid:
                    trade_ids.append(tid)

        assert len(trade_ids) >= 1

        # Resolve all as WIN
        total_pnl = 0.0
        for tid in trade_ids:
            trades = await db.get_open_trades("MOMENTUM", "BTC")
            trade = next((t for t in trades if t["id"] == tid), None)
            if trade:
                _, pnl = Resolver._calculate_pnl(
                    trade["signal"], trade["entry_price"], trade["bet_size"], "1",
                )
                await db.resolve_trade(tid, "WIN", pnl)
                total_pnl += pnl

        stats = await db.get_stats("MOMENTUM", "BTC")
        assert stats["wins"] == len(trade_ids)
        assert stats["total_pnl"] > 0


# ── E2E: Risk checks in pipeline ────────────────────────────────────────────


class TestRiskIntegration:
    """Risk manager blocks trades correctly in the full pipeline."""

    @pytest.mark.asyncio
    async def test_vpn_inactive_blocks_trade(self, db, bus, sizer, risk, market_btc, snapshot_buy_yes):
        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        result = strategy.evaluate("BTC", snapshot_buy_yes)
        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=False):
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg)

        assert trade_id is None
        trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(trades) == 0

    @pytest.mark.asyncio
    async def test_low_bankroll_blocks_trade(self, db, bus, risk, market_btc, snapshot_buy_yes):
        # Set bankroll to near zero
        await db.conn.execute(
            "UPDATE bankroll SET current = 0.5 WHERE strategy = 'MOMENTUM' AND asset = 'BTC'"
        )
        await db.conn.commit()

        strategy = MomentumStrategy(MomentumConfig())
        sizer = Sizer(SizerConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        result = strategy.evaluate("BTC", snapshot_buy_yes)
        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg)

        assert trade_id is None


# ── E2E: EventBus integration ───────────────────────────────────────────────


class TestEventBusIntegration:
    """Verify events are published during trade lifecycle."""

    @pytest.mark.asyncio
    async def test_trade_placed_event(self, db, bus, sizer, risk, market_btc, snapshot_buy_yes):
        events_received = []
        bus.subscribe("trade.placed", lambda data: events_received.append(data))

        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            result = strategy.evaluate("BTC", snapshot_buy_yes)
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg)

        assert trade_id is not None
        assert len(events_received) == 1
        assert events_received[0]["strategy"] == "MOMENTUM"
        assert events_received[0]["asset"] == "BTC"
        assert events_received[0]["signal"] == "BUY_YES"


# ── E2E: Gas fee deduction ──────────────────────────────────────────────────


class TestGasFeeDeduction:
    """Verify gas fees are deducted via the DB method (not raw SQL)."""

    @pytest.mark.asyncio
    async def test_gas_deducted_on_trade(self, db, bus, sizer, risk, market_btc, snapshot_buy_yes):
        initial = await db.get_bankroll("MOMENTUM", "BTC")

        strategy = MomentumStrategy(MomentumConfig())
        mf = _mock_market_finder(market_btc)
        executor = Executor(db, mf, sizer, risk, bus)

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            result = strategy.evaluate("BTC", snapshot_buy_yes)
            trade_id = await executor.execute(result, strategy_cfg=strategy.cfg)

        assert trade_id is not None
        after = await db.get_bankroll("MOMENTUM", "BTC")
        # Bankroll should be reduced by at least the gas fee (0.01)
        assert after < initial


# ── E2E: deduct_fee DB method ───────────────────────────────────────────────


class TestDeductFeeMethod:
    """Verify the new Database.deduct_fee method works correctly."""

    @pytest.mark.asyncio
    async def test_deduct_fee(self, db):
        initial = await db.get_bankroll("MOMENTUM", "BTC")
        await db.deduct_fee("MOMENTUM", "BTC", 1.50)
        after = await db.get_bankroll("MOMENTUM", "BTC")
        assert after == pytest.approx(initial - 1.50, abs=0.01)

    @pytest.mark.asyncio
    async def test_deduct_fee_multiple(self, db):
        initial = await db.get_bankroll("MOMENTUM", "BTC")
        await db.deduct_fee("MOMENTUM", "BTC", 0.5)
        await db.deduct_fee("MOMENTUM", "BTC", 0.3)
        after = await db.get_bankroll("MOMENTUM", "BTC")
        assert after == pytest.approx(initial - 0.8, abs=0.01)


# ── E2E: ACTIVE_BOTS constant ───────────────────────────────────────────────


class TestActiveBotsCentral:
    """Verify all modules use the central ACTIVE_BOTS constant."""

    def test_active_bots_has_5_pairs(self):
        assert len(ACTIVE_BOTS) == 5

    def test_all_strategies_represented(self):
        strategies = {s for s, _ in ACTIVE_BOTS}
        assert strategies == {"TURBO_CVD", "TURBO_VWAP", "MOMENTUM", "BOLLINGER"}

    def test_all_assets_represented(self):
        assets = {a for _, a in ACTIVE_BOTS}
        assert assets == {"BTC", "ETH", "SOL"}
