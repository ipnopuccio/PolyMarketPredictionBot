"""Tests for RiskManager -- drawdown, correlation, circuit breaker."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from bot.config import RiskConfig
from bot.execution.risk import RiskManager
from bot.storage.database import Database
from tests.conftest import insert_resolved_trades


STRATEGY = "MOMENTUM"
ASSET = "BTC"


def _risk(
    strategy_drawdown_disable: float = 0.25,
    portfolio_drawdown_pause: float = 0.20,
    max_same_direction_per_asset: int = 2,
    max_unidirectional_exposure_pct: float = 0.60,
    circuit_breaker_consecutive_losses: int = 5,
    circuit_breaker_cooldown_windows: int = 10,
) -> RiskManager:
    cfg = RiskConfig(
        strategy_drawdown_disable=strategy_drawdown_disable,
        portfolio_drawdown_pause=portfolio_drawdown_pause,
        max_same_direction_per_asset=max_same_direction_per_asset,
        max_unidirectional_exposure_pct=max_unidirectional_exposure_pct,
        circuit_breaker_consecutive_losses=circuit_breaker_consecutive_losses,
        circuit_breaker_cooldown_windows=circuit_breaker_cooldown_windows,
    )
    return RiskManager(cfg)


# ── Helpers to manipulate bankroll directly ──────────────────

async def _set_bankroll(db: Database, strategy: str, asset: str,
                        current: float, peak: float) -> None:
    await db.conn.execute(
        "UPDATE bankroll SET current=?, peak=? WHERE strategy=? AND asset=?",
        (current, peak, strategy, asset),
    )
    await db.conn.commit()


async def _insert_open_trade(
    db: Database, strategy: str, asset: str, signal: str,
    market_id: str = "mkt_test",
) -> int | None:
    """Insert an open (unresolved) trade."""
    from bot.core.types import FeedSnapshot
    snap = FeedSnapshot(connected=True, last_update=time.time())
    return await db.reserve_and_insert_trade(
        strategy=strategy, asset=asset, market_id=market_id,
        signal=signal, entry_price=0.50, bet_size=1.0,
        confidence=0.5, regime="UNKNOWN",
        snapshot=snap.to_dict(), rsi=None, bb_pct=None,
    )


# =========================================================================
# Drawdown
# =========================================================================

class TestDrawdown:
    """Strategy-level and portfolio-level drawdown checks."""

    async def test_no_drawdown_passes(self, db: Database):
        rm = _risk()
        result = await rm.check_drawdown(db, STRATEGY, ASSET)
        assert result is True

    async def test_strategy_drawdown_blocks(self, db: Database):
        """current = 30, peak = 40 => dd = 25% => blocked at 25% threshold."""
        await _set_bankroll(db, STRATEGY, ASSET, current=30.0, peak=40.0)
        rm = _risk(strategy_drawdown_disable=0.25)
        result = await rm.check_drawdown(db, STRATEGY, ASSET)
        assert result is False

    async def test_strategy_drawdown_below_threshold_passes(self, db: Database):
        """current = 35, peak = 40 => dd = 12.5% => passes at 25% threshold."""
        await _set_bankroll(db, STRATEGY, ASSET, current=35.0, peak=40.0)
        rm = _risk(strategy_drawdown_disable=0.25)
        result = await rm.check_drawdown(db, STRATEGY, ASSET)
        assert result is True

    async def test_portfolio_drawdown_blocks(self, db: Database):
        """Aggregate drawdown across all strategies for one asset."""
        # Set ALL 4 strategies for BTC in drawdown (conftest seeds all 4)
        for strat in ("MOMENTUM", "BOLLINGER", "TURBO_CVD", "TURBO_VWAP"):
            await _set_bankroll(db, strat, ASSET, current=25.0, peak=40.0)
        # Portfolio: current=100, peak=160 => 37.5% drawdown => blocked at 20%
        rm = _risk(
            strategy_drawdown_disable=0.50,  # high so strategy check passes
            portfolio_drawdown_pause=0.20,
        )
        result = await rm.check_drawdown(db, STRATEGY, ASSET)
        assert result is False

    async def test_portfolio_drawdown_passes_when_healthy(self, db: Database):
        """All bankrolls near peak -- portfolio drawdown is fine."""
        rm = _risk(portfolio_drawdown_pause=0.20)
        result = await rm.check_drawdown(db, STRATEGY, ASSET)
        assert result is True


# =========================================================================
# Correlation
# =========================================================================

class TestCorrelation:
    """Limits same-direction exposure per asset."""

    async def test_no_open_trades_passes(self, db: Database):
        rm = _risk()
        result = await rm.check_correlation(db, ASSET)
        assert result is True

    async def test_unidirectional_with_few_trades_passes(self, db: Database):
        """2 BUY_YES, 0 BUY_NO => 100% uni but only 2 total (< 3), passes."""
        await _insert_open_trade(db, STRATEGY, ASSET, "BUY_YES", "m1")
        await _insert_open_trade(db, "BOLLINGER", ASSET, "BUY_YES", "m2")
        rm = _risk(max_unidirectional_exposure_pct=0.60)
        result = await rm.check_correlation(db, ASSET)
        assert result is True  # total_open < 3

    async def test_high_unidirectional_blocks(self, db: Database):
        """3 BUY_YES, 0 BUY_NO => 100% uni > 60% and total >= 3 => blocked."""
        await _insert_open_trade(db, "MOMENTUM", ASSET, "BUY_YES", "m1")
        await _insert_open_trade(db, "BOLLINGER", ASSET, "BUY_YES", "m2")
        await _insert_open_trade(db, "TURBO_CVD", ASSET, "BUY_YES", "m3")
        rm = _risk(max_unidirectional_exposure_pct=0.60)
        result = await rm.check_correlation(db, ASSET)
        assert result is False

    async def test_balanced_directions_passes(self, db: Database):
        """2 BUY_YES + 2 BUY_NO => 50% uni, passes 60% threshold."""
        await _insert_open_trade(db, "MOMENTUM", ASSET, "BUY_YES", "m1")
        await _insert_open_trade(db, "BOLLINGER", ASSET, "BUY_YES", "m2")
        await _insert_open_trade(db, "TURBO_CVD", ASSET, "BUY_NO", "m3")
        await _insert_open_trade(db, "TURBO_VWAP", ASSET, "BUY_NO", "m4")
        rm = _risk(max_unidirectional_exposure_pct=0.60)
        result = await rm.check_correlation(db, ASSET)
        assert result is True


# =========================================================================
# Circuit Breaker
# =========================================================================

class TestCircuitBreaker:
    """Pauses trading after consecutive losses."""

    async def test_no_losses_passes(self, db: Database):
        rm = _risk(circuit_breaker_consecutive_losses=3)
        result = await rm.check_circuit_breaker(db, STRATEGY, ASSET)
        assert result is True

    async def test_consecutive_losses_triggers(self, db: Database):
        """Exactly N consecutive losses => triggers the circuit breaker."""
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=0, losses=3)
        rm = _risk(circuit_breaker_consecutive_losses=3)
        result = await rm.check_circuit_breaker(db, STRATEGY, ASSET)
        assert result is False

    async def test_mixed_outcomes_passes(self, db: Database):
        """2 losses + 1 win + 2 losses: last 3 are not all losses."""
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=0, losses=2)
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=1, losses=0)
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=0, losses=2)
        rm = _risk(circuit_breaker_consecutive_losses=5)
        # Last 5 outcomes are LOSS, LOSS, WIN, LOSS, LOSS (newest first)
        # Not all LOSS, so passes
        result = await rm.check_circuit_breaker(db, STRATEGY, ASSET)
        assert result is True

    async def test_cooldown_blocks_even_without_losses(self, db: Database):
        """Once triggered, the breaker blocks until cooldown expires."""
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=0, losses=3)
        rm = _risk(
            circuit_breaker_consecutive_losses=3,
            circuit_breaker_cooldown_windows=10,
        )
        # First call triggers
        result1 = await rm.check_circuit_breaker(db, STRATEGY, ASSET)
        assert result1 is False

        # Second call is still in cooldown
        result2 = await rm.check_circuit_breaker(db, STRATEGY, ASSET)
        assert result2 is False

    async def test_cooldown_expires(self, db: Database):
        """After cooldown time passes, circuit breaker allows trading again."""
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=0, losses=3)
        rm = _risk(
            circuit_breaker_consecutive_losses=3,
            circuit_breaker_cooldown_windows=1,
        )
        # Trigger the breaker
        result = await rm.check_circuit_breaker(db, STRATEGY, ASSET)
        assert result is False

        # Manually expire the cooldown
        key = (STRATEGY, ASSET)
        rm._cb_cooldown[key] = time.time() - 1

        # Now add a win so the consecutive check passes
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=1, losses=0)
        result = await rm.check_circuit_breaker(db, STRATEGY, ASSET)
        assert result is True


# =========================================================================
# check_all
# =========================================================================

class TestCheckAll:
    """check_all combines drawdown + correlation + circuit breaker."""

    async def test_all_pass(self, db: Database):
        rm = _risk()
        result = await rm.check_all(db, STRATEGY, ASSET)
        assert result is True

    async def test_drawdown_blocks_check_all(self, db: Database):
        await _set_bankroll(db, STRATEGY, ASSET, current=20.0, peak=40.0)
        rm = _risk(strategy_drawdown_disable=0.25)
        result = await rm.check_all(db, STRATEGY, ASSET)
        assert result is False
