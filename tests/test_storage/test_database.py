"""Tests for bot.storage.database — async SQLite layer."""
from __future__ import annotations

import pytest

from bot.core.types import FeedSnapshot
from bot.storage.database import Database
from tests.conftest import insert_resolved_trades


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot_dict(**overrides) -> dict:
    """Minimal snapshot dict for trade insertion."""
    snap = FeedSnapshot(connected=True, last_update=1.0)
    d = snap.to_dict()
    d.update(overrides)
    return d


async def _insert_open_trade(
    db: Database,
    strategy: str = "MOMENTUM",
    asset: str = "BTC",
    signal: str = "BUY_YES",
    entry_price: float = 0.45,
    bet_size: float = 1.0,
) -> int | None:
    return await db.reserve_and_insert_trade(
        strategy=strategy,
        asset=asset,
        market_id="mkt_001",
        signal=signal,
        entry_price=entry_price,
        bet_size=bet_size,
        confidence=0.8,
        regime="UNKNOWN",
        snapshot=_snapshot_dict(),
        rsi=55.0,
        bb_pct=0.6,
    )


# ---------------------------------------------------------------------------
# Schema & connection
# ---------------------------------------------------------------------------

class TestSchemaCreation:

    async def test_tables_created(self, db: Database):
        """All six tables exist after connect()."""
        expected = {"trades", "bankroll", "signal_state",
                    "price_history", "risk_events", "regime_history"}
        async with db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row["name"] for row in await cur.fetchall()}
        assert expected.issubset(tables)

    async def test_indexes_created(self, db: Database):
        """Key indexes exist on the trades table."""
        async with db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trades'"
        ) as cur:
            indexes = {row["name"] for row in await cur.fetchall()}
        assert "idx_trades_strategy_asset_outcome" in indexes
        assert "idx_trades_market_id" in indexes
        assert "idx_trades_outcome_null" in indexes

    async def test_wal_mode_enabled(self, db: Database):
        async with db.conn.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
        assert row[0] == "wal"


# ---------------------------------------------------------------------------
# Bankroll
# ---------------------------------------------------------------------------

class TestSeedBankroll:

    async def test_initial_bankroll(self, db: Database):
        bal = await db.get_bankroll("MOMENTUM", "BTC")
        assert bal == 40.0

    async def test_initial_peak_equals_current(self, db: Database):
        peak = await db.get_bankroll_peak("MOMENTUM", "BTC")
        assert peak == 40.0

    async def test_all_bankrolls_populated(self, db: Database):
        rows = await db.get_all_bankrolls()
        # 4 strategies x 3 assets = 12
        assert len(rows) == 12
        for row in rows:
            assert row["current"] == 40.0
            assert row["peak"] == 40.0

    async def test_seed_is_idempotent(self, db: Database):
        """Calling seed again with INSERT OR IGNORE does not overwrite."""
        await db.seed_bankroll(["MOMENTUM"], ["BTC"], 999.0)
        bal = await db.get_bankroll("MOMENTUM", "BTC")
        assert bal == 40.0  # unchanged — INSERT OR IGNORE

    async def test_missing_bankroll_returns_zero(self, db: Database):
        bal = await db.get_bankroll("NONEXISTENT", "BTC")
        assert bal == 0.0


# ---------------------------------------------------------------------------
# Reserve & insert trade
# ---------------------------------------------------------------------------

class TestReserveAndInsertTrade:

    async def test_trade_inserted(self, db: Database):
        tid = await _insert_open_trade(db)
        assert tid is not None and tid > 0

    async def test_bankroll_debited(self, db: Database):
        await _insert_open_trade(db, entry_price=0.50, bet_size=1.0)
        bal = await db.get_bankroll("MOMENTUM", "BTC")
        assert bal == pytest.approx(40.0 - 0.50)

    async def test_insufficient_bankroll_returns_none(self, db: Database):
        tid = await _insert_open_trade(db, entry_price=50.0, bet_size=1.0)
        assert tid is None

    async def test_snapshot_fields_stored(self, db: Database):
        snap = _snapshot_dict(cvd_2min=5000.0, funding_rate=0.0002)
        tid = await db.reserve_and_insert_trade(
            strategy="MOMENTUM", asset="BTC", market_id="m1",
            signal="BUY_YES", entry_price=0.45, bet_size=1.0,
            confidence=0.8, regime="TRENDING", snapshot=snap,
            rsi=60.0, bb_pct=0.7,
        )
        assert tid is not None
        async with db.conn.execute(
            "SELECT cvd_at_signal, funding_at_signal, rsi_at_signal, bb_pct_at_signal "
            "FROM trades WHERE id=?", (tid,)
        ) as cur:
            row = await cur.fetchone()
        assert row["cvd_at_signal"] == 5000.0
        assert row["funding_at_signal"] == 0.0002
        assert row["rsi_at_signal"] == 60.0
        assert row["bb_pct_at_signal"] == 0.7


# ---------------------------------------------------------------------------
# Resolve trade
# ---------------------------------------------------------------------------

class TestResolveTrade:

    async def test_resolve_win(self, db: Database):
        tid = await _insert_open_trade(db, entry_price=0.45, bet_size=1.0)
        assert tid is not None
        pnl = (1 - 0.45) * 1.0
        await db.resolve_trade(tid, "WIN", pnl)

        async with db.conn.execute(
            "SELECT outcome, pnl FROM trades WHERE id=?", (tid,)
        ) as cur:
            row = await cur.fetchone()
        assert row["outcome"] == "WIN"
        assert row["pnl"] == pytest.approx(pnl)

    async def test_resolve_restores_bankroll(self, db: Database):
        tid = await _insert_open_trade(db, entry_price=0.45, bet_size=1.0)
        assert tid is not None
        pnl = (1 - 0.45) * 1.0
        await db.resolve_trade(tid, "WIN", pnl)
        # bankroll = (40 - 0.45) + 0.45 + pnl = 40 + pnl
        bal = await db.get_bankroll("MOMENTUM", "BTC")
        assert bal == pytest.approx(40.0 + pnl)

    async def test_resolve_loss_decreases_bankroll(self, db: Database):
        tid = await _insert_open_trade(db, entry_price=0.45, bet_size=1.0)
        assert tid is not None
        pnl = -0.45
        await db.resolve_trade(tid, "LOSS", pnl)
        # bankroll = (40 - 0.45) + 0.45 + (-0.45) = 39.55
        bal = await db.get_bankroll("MOMENTUM", "BTC")
        assert bal == pytest.approx(39.55)

    async def test_resolve_nonexistent_trade_is_noop(self, db: Database):
        await db.resolve_trade(99999, "WIN", 1.0)
        # No crash, bankroll unchanged
        bal = await db.get_bankroll("MOMENTUM", "BTC")
        assert bal == 40.0


# ---------------------------------------------------------------------------
# Open trades
# ---------------------------------------------------------------------------

class TestGetOpenTrades:

    async def test_no_open_trades_initially(self, db: Database):
        trades = await db.get_open_trades()
        assert trades == []

    async def test_open_trade_appears(self, db: Database):
        await _insert_open_trade(db)
        trades = await db.get_open_trades()
        assert len(trades) == 1
        assert trades[0]["outcome"] is None

    async def test_filter_by_strategy(self, db: Database):
        await _insert_open_trade(db, strategy="MOMENTUM")
        await _insert_open_trade(db, strategy="BOLLINGER")
        trades = await db.get_open_trades(strategy="MOMENTUM")
        assert len(trades) == 1
        assert trades[0]["strategy"] == "MOMENTUM"

    async def test_resolved_trade_excluded(self, db: Database):
        tid = await _insert_open_trade(db)
        assert tid is not None
        await db.resolve_trade(tid, "WIN", 0.5)
        trades = await db.get_open_trades()
        assert trades == []


# ---------------------------------------------------------------------------
# Rolling stats
# ---------------------------------------------------------------------------

class TestRollingStats:

    async def test_empty_rolling_stats(self, db: Database):
        stats = await db.get_rolling_stats("MOMENTUM", "BTC")
        assert stats["resolved"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["avg_entry"] == 0.5

    async def test_rolling_stats_after_trades(self, db: Database):
        await insert_resolved_trades(db, "MOMENTUM", "BTC", wins=7, losses=3)
        stats = await db.get_rolling_stats("MOMENTUM", "BTC")
        assert stats["resolved"] == 10
        assert stats["win_rate"] == pytest.approx(0.7)
        assert stats["avg_entry"] == pytest.approx(0.45)


# ---------------------------------------------------------------------------
# Stats (full)
# ---------------------------------------------------------------------------

class TestStats:

    async def test_empty_stats(self, db: Database):
        stats = await db.get_stats("MOMENTUM", "BTC")
        assert stats["trades"] == 0
        assert stats["open"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["total_pnl"] == 0.0

    async def test_stats_with_resolved_and_open(self, db: Database):
        await insert_resolved_trades(db, "MOMENTUM", "BTC", wins=3, losses=1)
        await _insert_open_trade(db)
        stats = await db.get_stats("MOMENTUM", "BTC")
        assert stats["trades"] == 4  # resolved only
        assert stats["open"] == 1
        assert stats["wins"] == 3
        assert stats["win_rate"] == pytest.approx(75.0)  # percentage
        assert stats["total_pnl"] != 0.0


# ---------------------------------------------------------------------------
# Count open by direction
# ---------------------------------------------------------------------------

class TestCountOpenByDirection:

    async def test_no_open_trades(self, db: Database):
        counts = await db.count_open_by_direction("BTC")
        assert counts == {"BUY_YES": 0, "BUY_NO": 0}

    async def test_counts_both_directions(self, db: Database):
        await _insert_open_trade(db, signal="BUY_YES")
        await _insert_open_trade(db, signal="BUY_YES")
        await _insert_open_trade(db, signal="BUY_NO")
        counts = await db.count_open_by_direction("BTC")
        assert counts["BUY_YES"] == 2
        assert counts["BUY_NO"] == 1


# ---------------------------------------------------------------------------
# Risk events
# ---------------------------------------------------------------------------

class TestRiskEvents:

    async def test_log_and_retrieve_risk_event(self, db: Database):
        await db.log_risk_event("DRAWDOWN_PAUSE", "MOMENTUM", "BTC", "dd=-0.30")
        events = await db.get_recent_risk_events(limit=10)
        assert len(events) == 1
        assert events[0]["event_type"] == "DRAWDOWN_PAUSE"
        assert events[0]["details"] == "dd=-0.30"

    async def test_risk_events_ordered_desc(self, db: Database):
        await db.log_risk_event("A", None, None, "first")
        await db.log_risk_event("B", None, None, "second")
        events = await db.get_recent_risk_events(limit=10)
        assert events[0]["event_type"] == "B"
        assert events[1]["event_type"] == "A"


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

class TestPriceHistory:

    async def test_record_and_retrieve(self, db: Database):
        await db.record_price("BTC", 67_500.0)
        await db.record_price("BTC", 67_520.0)
        history = await db.get_price_history("BTC", limit=10)
        assert len(history) == 2
        # Returned in chronological order (oldest first)
        assert history[0]["price"] == 67_500.0
        assert history[1]["price"] == 67_520.0

    async def test_price_history_capped_at_200(self, db: Database):
        for i in range(210):
            await db.record_price("BTC", 60_000.0 + i)
        history = await db.get_price_history("BTC", limit=300)
        assert len(history) == 200


# ---------------------------------------------------------------------------
# Signal state
# ---------------------------------------------------------------------------

class TestSignalState:

    async def test_save_and_retrieve_signal_state(self, db: Database):
        snap = _snapshot_dict(last_price=67_500.0, cvd_2min=1_000_000.0)
        market_info = {"title": "BTC Up/Down 5m", "up_price": 0.52}
        await db.save_signal_state(
            "MOMENTUM", "BTC", "BUY_YES", 0.85, snap,
            rsi=55.0, bb_pct=0.6, regime="TRENDING",
            market_info=market_info,
        )
        states = await db.get_signal_states()
        assert len(states) == 1
        assert states[0]["strategy"] == "MOMENTUM"
        assert states[0]["signal"] == "BUY_YES"
        assert states[0]["rsi"] == 55.0
        assert states[0]["market_title"] == "BTC Up/Down 5m"

    async def test_signal_state_upserts(self, db: Database):
        snap = _snapshot_dict()
        market_info = {"title": "BTC Up/Down", "up_price": 0.50}
        await db.save_signal_state(
            "MOMENTUM", "BTC", "BUY_YES", 0.7, snap,
            rsi=50.0, bb_pct=0.5, regime="RANGING", market_info=market_info,
        )
        await db.save_signal_state(
            "MOMENTUM", "BTC", "BUY_NO", 0.9, snap,
            rsi=30.0, bb_pct=0.2, regime="TRENDING", market_info=market_info,
        )
        states = await db.get_signal_states()
        # Still one row — upserted
        assert len(states) == 1
        assert states[0]["signal"] == "BUY_NO"
        assert states[0]["rsi"] == 30.0
