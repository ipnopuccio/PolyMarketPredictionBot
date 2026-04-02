"""Stress tests — concurrent trade execution and database contention.

Verifies the bot handles high-throughput scenarios:
  - 100+ concurrent trade signals through the executor
  - Concurrent bankroll reads/writes
  - WindowTracker under contention
  - Resolver handling many open trades
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import MomentumConfig, RiskConfig, SizerConfig
from bot.core.events import EventBus
from bot.core.types import FeedSnapshot, MarketInfo, Signal, SignalResult
from bot.execution.executor import Executor, _WindowTracker
from bot.execution.resolver import Resolver
from bot.execution.risk import RiskManager
from bot.execution.sizer import Sizer
from bot.storage.database import Database


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_signal_result(strategy: str = "MOMENTUM", asset: str = "BTC",
                        signal: Signal = Signal.BUY_YES) -> SignalResult:
    snapshot = FeedSnapshot(
        last_price=67_500.0,
        cvd_2min=2_000_000.0,
        vwap_change=0.001,
        connected=True,
        last_update=time.time(),
    )
    return SignalResult(
        signal=signal,
        confidence=0.8,
        strategy=strategy,
        asset=asset,
        snapshot=snapshot,
        indicators={"cvd": 2_000_000.0, "vwap_change": 0.001},
    )


def _make_market(asset: str = "BTC", idx: int = 0) -> MarketInfo:
    return MarketInfo(
        asset=asset,
        market_id=f"0xstress_{asset}_{idx}",
        event_title=f"{asset} Up/Down 5m",
        up_token_id="tok_up",
        down_token_id="tok_down",
        up_price=0.52,
        down_price=0.48,
        window_start=int(time.time()),
        interval=300,
    )


def _mock_market_finder(asset: str = "BTC"):
    mf = MagicMock()
    call_count = 0

    async def _find_market(a):
        nonlocal call_count
        call_count += 1
        return _make_market(a, call_count)

    mf.find_market = AsyncMock(side_effect=_find_market)
    mf.window_elapsed_pct = MagicMock(return_value=0.3)
    mf.get_entry_price = AsyncMock(return_value=0.50)
    return mf


# ── Concurrent Trade Execution ──────────────────────────────────────────────


class TestConcurrentExecution:
    """Fire many trades concurrently and verify DB consistency."""

    @pytest.mark.asyncio
    async def test_50_concurrent_buy_yes(self, db):
        """50 concurrent BUY_YES signals — all should insert or be rate-limited."""
        bus = EventBus()
        risk = RiskManager(RiskConfig())
        sizer = Sizer(SizerConfig())
        mf = _mock_market_finder()
        executor = Executor(db, mf, sizer, risk, bus)

        cfg = MomentumConfig(max_orders_per_window=100)

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            tasks = [
                executor.execute(_make_signal_result(), strategy_cfg=cfg, scaling=True)
                for _ in range(50)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # No exceptions should be raised
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0, f"Got exceptions: {exceptions}"

        # At least some trades should have been placed
        successful = [r for r in results if r is not None]
        assert len(successful) > 0

        # Database should be consistent
        trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(trades) == len(successful)

    @pytest.mark.asyncio
    async def test_100_concurrent_mixed_signals(self, db):
        """100 concurrent signals across multiple strategies/assets."""
        bus = EventBus()
        risk = RiskManager(RiskConfig(max_same_direction_per_asset=200))
        sizer = Sizer(SizerConfig())
        mf = _mock_market_finder()
        executor = Executor(db, mf, sizer, risk, bus)

        cfg = MomentumConfig(max_orders_per_window=200)
        pairs = [
            ("MOMENTUM", "BTC", Signal.BUY_YES),
            ("MOMENTUM", "BTC", Signal.BUY_NO),
            ("TURBO_CVD", "ETH", Signal.BUY_YES),
            ("TURBO_VWAP", "ETH", Signal.BUY_NO),
        ]

        with patch("bot.execution.executor.is_vpn_active", new_callable=AsyncMock, return_value=True):
            tasks = []
            for i in range(100):
                strategy, asset, signal = pairs[i % len(pairs)]
                tasks.append(
                    executor.execute(
                        _make_signal_result(strategy, asset, signal),
                        strategy_cfg=cfg,
                        scaling=True,
                    )
                )
            results = await asyncio.gather(*tasks, return_exceptions=True)

        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0

        # Verify bankrolls are still positive
        for strategy in ["MOMENTUM", "TURBO_CVD", "TURBO_VWAP"]:
            for asset in ["BTC", "ETH"]:
                bankroll = await db.get_bankroll(strategy, asset)
                assert bankroll >= 0, f"{strategy}/{asset} bankroll negative: {bankroll}"


# ── WindowTracker Concurrency ───────────────────────────────────────────────


class TestWindowTrackerConcurrency:
    """WindowTracker under concurrent access."""

    @pytest.mark.asyncio
    async def test_concurrent_record(self):
        tracker = _WindowTracker()

        async def record_one():
            return await tracker.record("MOMENTUM", "BTC")

        results = await asyncio.gather(*[record_one() for _ in range(100)])

        # All counts should be sequential 1..100
        assert sorted(results) == list(range(1, 101))

        # Final count should be 100
        count = await tracker.count("MOMENTUM", "BTC")
        assert count == 100

    @pytest.mark.asyncio
    async def test_concurrent_can_place(self):
        tracker = _WindowTracker()

        # Record 10 orders
        for _ in range(10):
            await tracker.record("MOMENTUM", "BTC")

        # Concurrent checks: should all return False when max is 10
        checks = await asyncio.gather(*[
            tracker.can_place("MOMENTUM", "BTC", 10) for _ in range(50)
        ])
        assert all(c is False for c in checks)

        # Should all return True when max is 100
        checks = await asyncio.gather(*[
            tracker.can_place("MOMENTUM", "BTC", 100) for _ in range(50)
        ])
        assert all(c is True for c in checks)


# ── Concurrent Bankroll Operations ──────────────────────────────────────────


class TestConcurrentBankroll:
    """Database bankroll operations under contention."""

    @pytest.mark.asyncio
    async def test_concurrent_reads(self, db):
        """50 concurrent bankroll reads should all succeed."""
        results = await asyncio.gather(*[
            db.get_bankroll("MOMENTUM", "BTC") for _ in range(50)
        ])
        assert all(r == 40.0 for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_deduct_fee(self, db):
        """Sequential fee deductions maintain consistency."""
        initial = await db.get_bankroll("MOMENTUM", "BTC")
        n = 20
        fee = 0.01

        for _ in range(n):
            await db.deduct_fee("MOMENTUM", "BTC", fee)

        final = await db.get_bankroll("MOMENTUM", "BTC")
        assert final == pytest.approx(initial - n * fee, abs=0.01)


# ── Resolver with many open trades ──────────────────────────────────────────


class TestResolverBulk:
    """Resolver handling bulk trade resolution."""

    @pytest.mark.asyncio
    async def test_resolve_many_trades(self, db):
        """Insert 50 trades and resolve them all."""
        snapshot = FeedSnapshot(connected=True, last_update=time.time())
        trade_ids = []

        for i in range(50):
            tid = await db.reserve_and_insert_trade(
                strategy="MOMENTUM",
                asset="BTC",
                market_id=f"0xbulk_{i}",
                signal="BUY_YES",
                entry_price=0.50,
                bet_size=0.50,
                confidence=0.8,
                regime="UNKNOWN",
                snapshot=snapshot.to_dict(),
            )
            if tid is not None:
                trade_ids.append(tid)

        assert len(trade_ids) > 0

        # Resolve all as WIN
        for tid in trade_ids:
            _, pnl = Resolver._calculate_pnl("BUY_YES", 0.50, 0.50, "1")
            await db.resolve_trade(tid, "WIN", pnl)

        # All should be resolved
        open_trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(open_trades) == 0

        stats = await db.get_stats("MOMENTUM", "BTC")
        assert stats["wins"] == len(trade_ids)

    @pytest.mark.asyncio
    async def test_pnl_calculation_batch(self):
        """Batch PnL calculations should be consistent."""
        results = []
        for _ in range(100):
            outcome, pnl = Resolver._calculate_pnl(
                "BUY_YES", 0.50, 1.0, "1", taker_fee_pct=0.02,
            )
            results.append((outcome, pnl))

        # All should be identical
        assert all(r[0] == "WIN" for r in results)
        assert all(r[1] == results[0][1] for r in results)
        # (1 - 0.50) * 1.0 - 0.02 * 0.50 = 0.49
        assert results[0][1] == pytest.approx(0.49, abs=0.01)
