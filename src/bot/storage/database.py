"""Async SQLite database layer using aiosqlite."""
from __future__ import annotations

import aiosqlite
from datetime import datetime, timezone
from typing import Any


class Database:
    """Async SQLite wrapper with proper connection management."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._init_schema()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not connected. Call connect() first."
        return self._conn

    async def _init_schema(self) -> None:
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                strategy TEXT NOT NULL,
                asset TEXT NOT NULL,
                market_id TEXT,
                signal TEXT NOT NULL,
                entry_price REAL NOT NULL,
                bet_size REAL NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                regime TEXT NOT NULL DEFAULT 'UNKNOWN',
                outcome TEXT,
                pnl REAL,
                cvd_at_signal REAL,
                funding_at_signal REAL,
                liq_long_at_signal REAL,
                liq_short_at_signal REAL,
                vwap_change_at_signal REAL,
                rsi_at_signal REAL,
                bb_pct_at_signal REAL
            );

            CREATE INDEX IF NOT EXISTS idx_trades_strategy_asset_outcome
                ON trades(strategy, asset, outcome);
            CREATE INDEX IF NOT EXISTS idx_trades_market_id
                ON trades(market_id);
            CREATE INDEX IF NOT EXISTS idx_trades_outcome_null
                ON trades(outcome) WHERE outcome IS NULL;

            CREATE TABLE IF NOT EXISTS bankroll (
                strategy TEXT NOT NULL,
                asset TEXT NOT NULL,
                current REAL NOT NULL,
                peak REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (strategy, asset)
            );

            CREATE TABLE IF NOT EXISTS signal_state (
                strategy TEXT NOT NULL,
                asset TEXT NOT NULL,
                signal TEXT,
                confidence REAL,
                price REAL,
                cvd REAL,
                vwap_change REAL,
                funding_rate REAL,
                liq_long REAL,
                liq_short REAL,
                rsi REAL,
                bb_pct REAL,
                regime TEXT,
                market_title TEXT,
                market_up_price REAL,
                updated_at TEXT,
                PRIMARY KEY (strategy, asset)
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                asset TEXT NOT NULL,
                price REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS risk_events (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                strategy TEXT,
                asset TEXT,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS regime_history (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                asset TEXT NOT NULL,
                regime TEXT NOT NULL,
                adx REAL,
                bb_width REAL,
                ema_slope REAL
            );
        """)
        await self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ─── Bankroll ───────────────────────────────────────────

    async def seed_bankroll(self, strategies: list[str], assets: list[str],
                            initial: float) -> None:
        for strat in strategies:
            for asset in assets:
                await self.conn.execute(
                    "INSERT OR IGNORE INTO bankroll (strategy, asset, current, peak, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (strat, asset, initial, initial, self._now())
                )
        await self.conn.commit()

    async def get_bankroll(self, strategy: str, asset: str) -> float:
        async with self.conn.execute(
            "SELECT current FROM bankroll WHERE strategy=? AND asset=?",
            (strategy, asset)
        ) as cur:
            row = await cur.fetchone()
            return row["current"] if row else 0.0

    async def get_bankroll_peak(self, strategy: str, asset: str) -> float:
        async with self.conn.execute(
            "SELECT peak FROM bankroll WHERE strategy=? AND asset=?",
            (strategy, asset)
        ) as cur:
            row = await cur.fetchone()
            return row["peak"] if row else 0.0

    async def get_all_bankrolls(self) -> list[dict]:
        async with self.conn.execute(
            "SELECT strategy, asset, current, peak FROM bankroll"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def deduct_fee(self, strategy: str, asset: str, amount: float) -> None:
        """Deduct a fee (e.g. gas) from a strategy's bankroll."""
        await self.conn.execute(
            "UPDATE bankroll SET current = current - ? WHERE strategy = ? AND asset = ?",
            (amount, strategy, asset),
        )
        await self.conn.commit()

    # ─── Trades ─────────────────────────────────────────────

    async def reserve_and_insert_trade(
        self, strategy: str, asset: str, market_id: str | None,
        signal: str, entry_price: float, bet_size: float,
        confidence: float, regime: str, snapshot: dict,
        rsi: float | None = None, bb_pct: float | None = None
    ) -> int | None:
        bankroll_row = await (await self.conn.execute(
            "SELECT current FROM bankroll WHERE strategy=? AND asset=?",
            (strategy, asset)
        )).fetchone()
        if not bankroll_row:
            return None

        usdc_cost = entry_price * bet_size
        if bankroll_row["current"] < usdc_cost:
            return None

        await self.conn.execute(
            "UPDATE bankroll SET current=?, updated_at=? WHERE strategy=? AND asset=?",
            (bankroll_row["current"] - usdc_cost, self._now(), strategy, asset)
        )
        cur = await self.conn.execute(
            """INSERT INTO trades
               (timestamp, strategy, asset, market_id, signal, entry_price, bet_size,
                confidence, regime,
                cvd_at_signal, funding_at_signal, liq_long_at_signal,
                liq_short_at_signal, vwap_change_at_signal, rsi_at_signal, bb_pct_at_signal)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (self._now(), strategy, asset, market_id, signal, entry_price, bet_size,
             confidence, regime,
             snapshot.get("cvd_2min"), snapshot.get("funding_rate"),
             snapshot.get("liq_long_2min"), snapshot.get("liq_short_2min"),
             snapshot.get("vwap_change"), rsi, bb_pct)
        )
        await self.conn.commit()
        return cur.lastrowid

    async def resolve_trade(self, trade_id: int, outcome: str, pnl: float) -> None:
        trade = await (await self.conn.execute(
            "SELECT strategy, asset, entry_price, bet_size FROM trades WHERE id=?",
            (trade_id,)
        )).fetchone()
        if not trade:
            return

        await self.conn.execute(
            "UPDATE trades SET outcome=?, pnl=? WHERE id=?",
            (outcome, pnl, trade_id)
        )

        bankroll = await (await self.conn.execute(
            "SELECT current, peak FROM bankroll WHERE strategy=? AND asset=?",
            (trade["strategy"], trade["asset"])
        )).fetchone()

        usdc_cost = trade["entry_price"] * trade["bet_size"]
        new_bal = (bankroll["current"] if bankroll else 0) + usdc_cost + pnl
        new_peak = max(bankroll["peak"] if bankroll else 0, new_bal)

        await self.conn.execute(
            "UPDATE bankroll SET current=?, peak=?, updated_at=? WHERE strategy=? AND asset=?",
            (new_bal, new_peak, self._now(), trade["strategy"], trade["asset"])
        )
        await self.conn.commit()

    async def get_open_trades(self, strategy: str | None = None,
                               asset: str | None = None) -> list[dict]:
        query = "SELECT * FROM trades WHERE outcome IS NULL"
        params: list[Any] = []
        if strategy:
            query += " AND strategy=?"
            params.append(strategy)
        if asset:
            query += " AND asset=?"
            params.append(asset)

        async with self.conn.execute(query, params) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_rolling_stats(self, strategy: str, asset: str,
                                 n: int = 50) -> dict:
        async with self.conn.execute(
            "SELECT outcome, entry_price FROM trades "
            "WHERE strategy=? AND asset=? AND outcome IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (strategy, asset, n)
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            return {"resolved": 0, "win_rate": 0.0, "avg_entry": 0.5}
        wins = sum(1 for r in rows if r["outcome"] == "WIN")
        total = len(rows)
        return {
            "resolved": total,
            "win_rate": wins / total,
            "avg_entry": sum(r["entry_price"] for r in rows) / total,
        }

    async def get_recent_outcomes(self, strategy: str, asset: str,
                                   n: int = 5) -> list[str]:
        """Last N outcomes for streak detection."""
        async with self.conn.execute(
            "SELECT outcome FROM trades "
            "WHERE strategy=? AND asset=? AND outcome IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (strategy, asset, n)
        ) as cur:
            return [r["outcome"] for r in await cur.fetchall()]

    async def get_stats(self, strategy: str, asset: str) -> dict:
        async with self.conn.execute(
            "SELECT outcome, entry_price, bet_size, pnl FROM trades "
            "WHERE strategy=? AND asset=? AND outcome IS NOT NULL",
            (strategy, asset)
        ) as cur:
            rows = await cur.fetchall()

        async with self.conn.execute(
            "SELECT COUNT(*) as n FROM trades WHERE strategy=? AND asset=? AND outcome IS NULL",
            (strategy, asset)
        ) as cur:
            open_count = (await cur.fetchone())["n"]

        if not rows:
            return {"trades": 0, "open": open_count, "win_rate": 0.0,
                    "avg_entry": 0.0, "total_pnl": 0.0, "edge": 0.0, "wins": 0}
        wins = sum(1 for r in rows if r["outcome"] == "WIN")
        total = len(rows)
        avg_entry = sum(r["entry_price"] for r in rows) / total
        total_pnl = sum(r["pnl"] for r in rows if r["pnl"] is not None)
        win_rate = wins / total
        return {
            "trades": total, "open": open_count, "wins": wins,
            "win_rate": round(win_rate * 100, 1),
            "avg_entry": round(avg_entry, 3),
            "total_pnl": round(total_pnl, 2),
            "edge": round(win_rate - avg_entry, 4),
        }

    async def get_all_stats(self, strategies: list[str],
                             assets: list[str]) -> list[dict]:
        results = []
        for strat in strategies:
            for asset in assets:
                stats = await self.get_stats(strat, asset)
                bankroll = await self.get_bankroll(strat, asset)
                results.append({"strategy": strat, "asset": asset,
                                "bankroll": round(bankroll, 2), **stats})
        return results

    # ─── Signal State ───────────────────────────────────────

    async def save_signal_state(
        self, strategy: str, asset: str, signal: str,
        confidence: float, snapshot: dict, rsi: float | None,
        bb_pct: float | None, regime: str, market_info: dict
    ) -> None:
        await self.conn.execute("""
            INSERT INTO signal_state
              (strategy, asset, signal, confidence, price, cvd, vwap_change, funding_rate,
               liq_long, liq_short, rsi, bb_pct, regime, market_title, market_up_price, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(strategy, asset) DO UPDATE SET
              signal=excluded.signal, confidence=excluded.confidence,
              price=excluded.price, cvd=excluded.cvd,
              vwap_change=excluded.vwap_change, funding_rate=excluded.funding_rate,
              liq_long=excluded.liq_long, liq_short=excluded.liq_short,
              rsi=excluded.rsi, bb_pct=excluded.bb_pct, regime=excluded.regime,
              market_title=excluded.market_title, market_up_price=excluded.market_up_price,
              updated_at=excluded.updated_at
        """, (
            strategy, asset, signal, confidence,
            snapshot.get("last_price"), snapshot.get("cvd_2min"),
            snapshot.get("vwap_change"), snapshot.get("funding_rate"),
            snapshot.get("liq_long_2min"), snapshot.get("liq_short_2min"),
            rsi, bb_pct, regime,
            market_info.get("title"), market_info.get("up_price"),
            self._now()
        ))
        await self.conn.commit()

    async def get_signal_states(self) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM signal_state ORDER BY strategy, asset"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ─── Price History ──────────────────────────────────────

    async def record_price(self, asset: str, price: float) -> None:
        await self.conn.execute(
            "INSERT INTO price_history (timestamp, asset, price) VALUES (?,?,?)",
            (self._now(), asset, price)
        )
        await self.conn.execute(
            "DELETE FROM price_history WHERE asset=? AND id NOT IN "
            "(SELECT id FROM price_history WHERE asset=? ORDER BY id DESC LIMIT 200)",
            (asset, asset)
        )
        await self.conn.commit()

    async def get_price_history(self, asset: str, limit: int = 150) -> list[dict]:
        async with self.conn.execute(
            "SELECT timestamp, price FROM price_history WHERE asset=? "
            "ORDER BY id DESC LIMIT ?", (asset, limit)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in reversed(rows)]

    # ─── Risk Events ────────────────────────────────────────

    async def log_risk_event(self, event_type: str, strategy: str | None,
                              asset: str | None, details: str) -> None:
        await self.conn.execute(
            "INSERT INTO risk_events (timestamp, event_type, strategy, asset, details) "
            "VALUES (?,?,?,?,?)",
            (self._now(), event_type, strategy, asset, details)
        )
        await self.conn.commit()

    async def get_recent_risk_events(self, limit: int = 50) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM risk_events ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ─── Regime History ─────────────────────────────────────

    async def save_regime(self, asset: str, regime: str,
                           adx: float | None, bb_width: float | None,
                           ema_slope: float | None) -> None:
        await self.conn.execute(
            "INSERT INTO regime_history (timestamp, asset, regime, adx, bb_width, ema_slope) "
            "VALUES (?,?,?,?,?,?)",
            (self._now(), asset, regime, adx, bb_width, ema_slope)
        )
        await self.conn.commit()

    # ─── Recent Trades ──────────────────────────────────────

    async def get_recent_trades(self, limit: int = 100) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_trades_for_strategy(self, strategy: str,
                                       limit: int = 500) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM trades WHERE strategy=? ORDER BY id DESC LIMIT ?",
            (strategy, limit)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ─── Active positions count per direction ───────────────

    async def count_open_by_direction(self, asset: str) -> dict[str, int]:
        result = {"BUY_YES": 0, "BUY_NO": 0}
        async with self.conn.execute(
            "SELECT signal, COUNT(*) as n FROM trades "
            "WHERE asset=? AND outcome IS NULL GROUP BY signal",
            (asset,)
        ) as cur:
            for row in await cur.fetchall():
                result[row["signal"]] = row["n"]
        return result
