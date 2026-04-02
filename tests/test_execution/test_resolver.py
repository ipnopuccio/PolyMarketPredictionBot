"""Tests for the Resolver -- PnL calculation, resolution inference, and resolve cycle."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from bot.core.events import EventBus
from bot.core.types import FeedSnapshot
from bot.execution.resolver import Resolver
from bot.storage.database import Database


# =========================================================================
# _calculate_pnl (static, sync)
# =========================================================================

class TestCalculatePnl:
    """Resolver._calculate_pnl is a pure static function."""

    def test_buy_yes_wins_when_yes_resolves(self):
        outcome, pnl = Resolver._calculate_pnl(
            signal="BUY_YES", entry_price=0.50, bet_size=10.0,
            resolution="1", taker_fee_pct=0.02,
        )
        assert outcome == "WIN"
        # gross = (1-0.5)*10 = 5.0, fee = 5.0*0.02 = 0.10, net = 4.90
        assert round(pnl, 2) == 4.90

    def test_buy_yes_loses_when_no_resolves(self):
        outcome, pnl = Resolver._calculate_pnl(
            signal="BUY_YES", entry_price=0.50, bet_size=10.0,
            resolution="0", taker_fee_pct=0.02,
        )
        assert outcome == "LOSS"
        # loss = -entry * bet = -0.5 * 10 = -5.0
        assert round(pnl, 2) == -5.00

    def test_buy_no_wins_when_no_resolves(self):
        outcome, pnl = Resolver._calculate_pnl(
            signal="BUY_NO", entry_price=0.45, bet_size=8.0,
            resolution="0", taker_fee_pct=0.02,
        )
        assert outcome == "WIN"
        gross = (1 - 0.45) * 8.0
        fee = gross * 0.02
        assert round(pnl, 2) == round(gross - fee, 2)

    def test_buy_no_loses_when_yes_resolves(self):
        outcome, pnl = Resolver._calculate_pnl(
            signal="BUY_NO", entry_price=0.45, bet_size=8.0,
            resolution="1", taker_fee_pct=0.02,
        )
        assert outcome == "LOSS"
        assert round(pnl, 2) == round(-0.45 * 8.0, 2)

    def test_zero_fee(self):
        outcome, pnl = Resolver._calculate_pnl(
            signal="BUY_YES", entry_price=0.40, bet_size=5.0,
            resolution="1", taker_fee_pct=0.0,
        )
        assert outcome == "WIN"
        assert round(pnl, 2) == 3.00  # (1-0.4)*5 = 3.0

    def test_high_entry_price_small_profit(self):
        outcome, pnl = Resolver._calculate_pnl(
            signal="BUY_YES", entry_price=0.95, bet_size=100.0,
            resolution="1", taker_fee_pct=0.02,
        )
        assert outcome == "WIN"
        gross = (1 - 0.95) * 100.0  # 5.0
        assert round(pnl, 2) == round(gross * 0.98, 2)


# =========================================================================
# _infer_resolution (static, sync)
# =========================================================================

class TestInferResolution:
    """Resolver._infer_resolution reads the Gamma API market dict."""

    def test_explicit_resolution(self):
        market = {"resolution": "1", "closed": True}
        assert Resolver._infer_resolution(market) == "1"

    def test_explicit_resolution_zero(self):
        market = {"resolution": "0", "closed": True}
        assert Resolver._infer_resolution(market) == "0"

    def test_not_closed_returns_none(self):
        market = {"closed": False}
        assert Resolver._infer_resolution(market) is None

    def test_infer_from_outcome_prices_up_won(self):
        market = {"closed": True, "outcomePrices": ["1.0", "0.0"]}
        assert Resolver._infer_resolution(market) == "1"

    def test_infer_from_outcome_prices_down_won(self):
        market = {"closed": True, "outcomePrices": ["0.0", "1.0"]}
        assert Resolver._infer_resolution(market) == "0"

    def test_outcome_prices_as_json_string(self):
        """outcomePrices can arrive as a JSON string."""
        import json
        market = {"closed": True, "outcomePrices": json.dumps(["1.0", "0.0"])}
        assert Resolver._infer_resolution(market) == "1"

    def test_ambiguous_prices_returns_none(self):
        """Prices not near 0/1 -- market not yet settled."""
        market = {"closed": True, "outcomePrices": ["0.6", "0.4"]}
        assert Resolver._infer_resolution(market) is None

    def test_empty_prices_returns_none(self):
        market = {"closed": True, "outcomePrices": []}
        assert Resolver._infer_resolution(market) is None


# =========================================================================
# _resolve_cycle (async, needs mocked httpx + real db)
# =========================================================================

async def _insert_open_trade(db: Database, strategy: str, asset: str,
                              signal: str, market_id: str) -> int | None:
    snap = FeedSnapshot(connected=True, last_update=time.time())
    return await db.reserve_and_insert_trade(
        strategy=strategy, asset=asset, market_id=market_id,
        signal=signal, entry_price=0.50, bet_size=2.0,
        confidence=0.7, regime="UNKNOWN",
        snapshot=snap.to_dict(), rsi=None, bb_pct=None,
    )


class TestResolveCycle:
    """Integration: _resolve_cycle with mocked HTTP, real DB."""

    async def test_no_open_trades_is_noop(self, db: Database, event_bus: EventBus):
        resolver = Resolver(db, event_bus)
        # Should return without errors
        await resolver._resolve_cycle()

    async def test_resolved_market_updates_trade(self, db: Database, event_bus: EventBus):
        """Open trade on a resolved market gets WIN/LOSS and P&L."""
        tid = await _insert_open_trade(db, "MOMENTUM", "BTC", "BUY_YES", "mkt_001")
        assert tid is not None

        # Mock the HTTP fetch to return a resolved market
        resolved_market = {
            "resolution": "1",
            "closed": True,
        }
        resolver = Resolver(db, event_bus)
        with patch.object(resolver, "_fetch_market", new_callable=AsyncMock,
                          return_value=resolved_market):
            await resolver._resolve_cycle()

        # Trade should now be resolved
        open_trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(open_trades) == 0

        stats = await db.get_stats("MOMENTUM", "BTC")
        assert stats["trades"] == 1
        assert stats["wins"] == 1

    async def test_unresolved_market_keeps_trade_open(self, db: Database, event_bus: EventBus):
        """If the market is not yet closed, the trade stays open."""
        tid = await _insert_open_trade(db, "MOMENTUM", "BTC", "BUY_YES", "mkt_002")
        assert tid is not None

        unresolved = {"closed": False}
        resolver = Resolver(db, event_bus)
        with patch.object(resolver, "_fetch_market", new_callable=AsyncMock,
                          return_value=unresolved):
            await resolver._resolve_cycle()

        open_trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(open_trades) == 1

    async def test_fetch_failure_skips_market(self, db: Database, event_bus: EventBus):
        """If _fetch_market returns None, trades on that market are skipped."""
        tid = await _insert_open_trade(db, "MOMENTUM", "BTC", "BUY_YES", "mkt_003")
        assert tid is not None

        resolver = Resolver(db, event_bus)
        with patch.object(resolver, "_fetch_market", new_callable=AsyncMock,
                          return_value=None):
            await resolver._resolve_cycle()

        open_trades = await db.get_open_trades("MOMENTUM", "BTC")
        assert len(open_trades) == 1

    async def test_publishes_event_on_resolution(self, db: Database, event_bus: EventBus):
        """Resolver publishes trade.resolved event on the bus."""
        tid = await _insert_open_trade(db, "MOMENTUM", "BTC", "BUY_YES", "mkt_004")
        assert tid is not None

        events_received: list[dict] = []

        async def capture(data: dict) -> None:
            events_received.append(data)

        event_bus.subscribe("trade.resolved", capture)

        resolved_market = {"resolution": "1", "closed": True}
        resolver = Resolver(db, event_bus)
        with patch.object(resolver, "_fetch_market", new_callable=AsyncMock,
                          return_value=resolved_market):
            await resolver._resolve_cycle()

        assert len(events_received) == 1
        assert events_received[0]["outcome"] == "WIN"
        assert events_received[0]["trade_id"] == tid

    async def test_groups_trades_by_market(self, db: Database, event_bus: EventBus):
        """Multiple trades on the same market_id only trigger one fetch."""
        await _insert_open_trade(db, "MOMENTUM", "BTC", "BUY_YES", "mkt_shared")
        await _insert_open_trade(db, "BOLLINGER", "BTC", "BUY_NO", "mkt_shared")

        resolved_market = {"resolution": "1", "closed": True}
        resolver = Resolver(db, event_bus)
        mock_fetch = AsyncMock(return_value=resolved_market)
        with patch.object(resolver, "_fetch_market", mock_fetch):
            await resolver._resolve_cycle()

        # Only one call to _fetch_market, not two
        assert mock_fetch.call_count == 1

        # Both trades should be resolved
        open_trades = await db.get_open_trades()
        assert len(open_trades) == 0
