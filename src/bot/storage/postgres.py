"""Async PostgreSQL database layer using asyncpg.

Drop-in replacement for the SQLite ``Database`` class. Uses a connection pool
for concurrency and converts all ``asyncpg.Record`` objects to plain dicts so
callers see the same interface regardless of backend.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg

log = logging.getLogger(__name__)


class PostgresDatabase:
    """Async PostgreSQL wrapper backed by an ``asyncpg`` connection pool.

    Public API mirrors ``Database`` in ``database.py`` exactly.
    """

    def __init__(self, db_url: str, *, min_pool: int = 2, max_pool: int = 10) -> None:
        """Initialise with a PostgreSQL DSN.

        Args:
            db_url: PostgreSQL connection string,
                e.g. ``postgresql://user:pass@host:5432/dbname``.
            min_pool: Minimum idle connections kept in the pool.
            max_pool: Maximum connections the pool may open.
        """
        self._dsn = db_url
        self._min_pool = min_pool
        self._max_pool = max_pool
        self._pool: asyncpg.Pool | None = None

    # ── lifecycle ───────────────────────────────────────────

    async def connect(self) -> None:
        """Create the connection pool and initialise the schema."""
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_pool,
            max_size=self._max_pool,
        )
        await self._init_schema()
        log.info("PostgresDatabase connected (pool %s-%s)", self._min_pool, self._max_pool)

    async def close(self) -> None:
        """Gracefully close every connection in the pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            log.info("PostgresDatabase pool closed")

    @property
    def pool(self) -> asyncpg.Pool:
        """Return the live pool or raise if ``connect()`` was not called."""
        assert self._pool is not None, "Database not connected. Call connect() first."
        return self._pool

    # ── schema ──────────────────────────────────────────────

    async def _init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    strategy TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    market_id TEXT,
                    signal TEXT NOT NULL,
                    entry_price DOUBLE PRECISION NOT NULL,
                    bet_size DOUBLE PRECISION NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                    regime TEXT NOT NULL DEFAULT 'UNKNOWN',
                    outcome TEXT,
                    pnl DOUBLE PRECISION,
                    cvd_at_signal DOUBLE PRECISION,
                    funding_at_signal DOUBLE PRECISION,
                    liq_long_at_signal DOUBLE PRECISION,
                    liq_short_at_signal DOUBLE PRECISION,
                    vwap_change_at_signal DOUBLE PRECISION,
                    rsi_at_signal DOUBLE PRECISION,
                    bb_pct_at_signal DOUBLE PRECISION
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_strategy_asset_outcome
                    ON trades(strategy, asset, outcome);
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_market_id
                    ON trades(market_id);
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_outcome_null
                    ON trades(outcome) WHERE outcome IS NULL;
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bankroll (
                    strategy TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    current DOUBLE PRECISION NOT NULL,
                    peak DOUBLE PRECISION NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (strategy, asset)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_state (
                    strategy TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    signal TEXT,
                    confidence DOUBLE PRECISION,
                    price DOUBLE PRECISION,
                    cvd DOUBLE PRECISION,
                    vwap_change DOUBLE PRECISION,
                    funding_rate DOUBLE PRECISION,
                    liq_long DOUBLE PRECISION,
                    liq_short DOUBLE PRECISION,
                    rsi DOUBLE PRECISION,
                    bb_pct DOUBLE PRECISION,
                    regime TEXT,
                    market_title TEXT,
                    market_up_price DOUBLE PRECISION,
                    updated_at TIMESTAMPTZ,
                    PRIMARY KEY (strategy, asset)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    asset TEXT NOT NULL,
                    price DOUBLE PRECISION NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_events (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    event_type TEXT NOT NULL,
                    strategy TEXT,
                    asset TEXT,
                    details TEXT
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS regime_history (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ NOT NULL,
                    asset TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    adx DOUBLE PRECISION,
                    bb_width DOUBLE PRECISION,
                    ema_slope DOUBLE PRECISION
                );
            """)
        log.info("PostgresDatabase schema initialised")

    # ── helpers ──────────────────────────────────────────────

    @staticmethod
    def _now() -> datetime:
        """UTC-aware datetime for PostgreSQL TIMESTAMPTZ columns."""
        return datetime.now(timezone.utc)

    @staticmethod
    def _row(record: asyncpg.Record) -> dict[str, Any]:
        """Convert a single ``asyncpg.Record`` to a plain dict."""
        return dict(record)

    @staticmethod
    def _rows(records: list[asyncpg.Record]) -> list[dict[str, Any]]:
        """Convert a list of ``asyncpg.Record`` to a list of dicts."""
        return [dict(r) for r in records]

    # ─── Bankroll ───────────────────────────────────────────

    async def seed_bankroll(
        self,
        strategies: list[str],
        assets: list[str],
        initial: float,
    ) -> None:
        """Seed bankroll rows for every (strategy, asset) pair.

        Uses ``ON CONFLICT DO NOTHING`` so existing rows are untouched.

        Args:
            strategies: List of strategy names.
            assets: List of asset tickers.
            initial: Starting bankroll value for new pairs.
        """
        async with self.pool.acquire() as conn:
            for strat in strategies:
                for asset in assets:
                    await conn.execute(
                        "INSERT INTO bankroll (strategy, asset, current, peak, updated_at) "
                        "VALUES ($1, $2, $3, $4, $5) "
                        "ON CONFLICT (strategy, asset) DO NOTHING",
                        strat, asset, initial, initial, self._now(),
                    )

    async def get_bankroll(self, strategy: str, asset: str) -> float:
        """Return current bankroll for a strategy/asset pair (0.0 if missing).

        Args:
            strategy: Strategy name.
            asset: Asset ticker.

        Returns:
            Current bankroll balance.
        """
        row = await self.pool.fetchrow(
            "SELECT current FROM bankroll WHERE strategy=$1 AND asset=$2",
            strategy, asset,
        )
        return row["current"] if row else 0.0

    async def get_bankroll_peak(self, strategy: str, asset: str) -> float:
        """Return peak bankroll for a strategy/asset pair (0.0 if missing).

        Args:
            strategy: Strategy name.
            asset: Asset ticker.

        Returns:
            Peak bankroll value.
        """
        row = await self.pool.fetchrow(
            "SELECT peak FROM bankroll WHERE strategy=$1 AND asset=$2",
            strategy, asset,
        )
        return row["peak"] if row else 0.0

    async def get_all_bankrolls(self) -> list[dict]:
        """Return all bankroll rows as dicts.

        Returns:
            List of dicts with keys ``strategy``, ``asset``, ``current``, ``peak``.
        """
        rows = await self.pool.fetch(
            "SELECT strategy, asset, current, peak FROM bankroll"
        )
        return self._rows(rows)

    async def deduct_fee(self, strategy: str, asset: str, amount: float) -> None:
        """Deduct a fee (e.g. gas) from a strategy's bankroll."""
        await self.pool.execute(
            "UPDATE bankroll SET current = current - $1 WHERE strategy = $2 AND asset = $3",
            amount, strategy, asset,
        )

    # ─── Trades ─────────────────────────────────────────────

    async def reserve_and_insert_trade(
        self,
        strategy: str,
        asset: str,
        market_id: str | None,
        signal: str,
        entry_price: float,
        bet_size: float,
        confidence: float,
        regime: str,
        snapshot: dict,
        rsi: float | None = None,
        bb_pct: float | None = None,
    ) -> int | None:
        """Atomically reserve bankroll and insert a new trade.

        Inside a single transaction the method:
        1. Checks sufficient bankroll.
        2. Debits the bankroll.
        3. Inserts the trade row.
        4. Returns the new trade id.

        Args:
            strategy: Strategy name.
            asset: Asset ticker.
            market_id: Polymarket market identifier (may be ``None``).
            signal: Trade signal (``BUY_YES`` / ``BUY_NO``).
            entry_price: Entry price paid.
            bet_size: Number of contracts.
            confidence: Model confidence score.
            regime: Current market regime label.
            snapshot: Feed snapshot dict with indicator values.
            rsi: RSI-14 value at signal time.
            bb_pct: Bollinger Band %B value at signal time.

        Returns:
            The new trade ``id``, or ``None`` if bankroll is insufficient.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                bankroll_row = await conn.fetchrow(
                    "SELECT current FROM bankroll WHERE strategy=$1 AND asset=$2",
                    strategy, asset,
                )
                if not bankroll_row:
                    return None

                usdc_cost = entry_price * bet_size
                if bankroll_row["current"] < usdc_cost:
                    return None

                await conn.execute(
                    "UPDATE bankroll SET current=$1, updated_at=$2 "
                    "WHERE strategy=$3 AND asset=$4",
                    bankroll_row["current"] - usdc_cost, self._now(),
                    strategy, asset,
                )
                row = await conn.fetchrow(
                    """INSERT INTO trades
                       (timestamp, strategy, asset, market_id, signal,
                        entry_price, bet_size, confidence, regime,
                        cvd_at_signal, funding_at_signal,
                        liq_long_at_signal, liq_short_at_signal,
                        vwap_change_at_signal, rsi_at_signal, bb_pct_at_signal)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                       RETURNING id""",
                    self._now(), strategy, asset, market_id, signal,
                    entry_price, bet_size, confidence, regime,
                    snapshot.get("cvd_2min"), snapshot.get("funding_rate"),
                    snapshot.get("liq_long_2min"), snapshot.get("liq_short_2min"),
                    snapshot.get("vwap_change"), rsi, bb_pct,
                )
                return row["id"] if row else None

    async def resolve_trade(self, trade_id: int, outcome: str, pnl: float) -> None:
        """Mark a trade as resolved and credit bankroll.

        Args:
            trade_id: Primary key of the trade.
            outcome: ``"WIN"`` or ``"LOSS"``.
            pnl: Profit/loss in USDC (signed).
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                trade = await conn.fetchrow(
                    "SELECT strategy, asset, entry_price, bet_size FROM trades WHERE id=$1",
                    trade_id,
                )
                if not trade:
                    return

                await conn.execute(
                    "UPDATE trades SET outcome=$1, pnl=$2 WHERE id=$3",
                    outcome, pnl, trade_id,
                )

                bankroll = await conn.fetchrow(
                    "SELECT current, peak FROM bankroll WHERE strategy=$1 AND asset=$2",
                    trade["strategy"], trade["asset"],
                )

                usdc_cost = trade["entry_price"] * trade["bet_size"]
                new_bal = (bankroll["current"] if bankroll else 0) + usdc_cost + pnl
                new_peak = max(bankroll["peak"] if bankroll else 0, new_bal)

                await conn.execute(
                    "UPDATE bankroll SET current=$1, peak=$2, updated_at=$3 "
                    "WHERE strategy=$4 AND asset=$5",
                    new_bal, new_peak, self._now(),
                    trade["strategy"], trade["asset"],
                )

    async def get_open_trades(
        self,
        strategy: str | None = None,
        asset: str | None = None,
    ) -> list[dict]:
        """Return all open (unresolved) trades, optionally filtered.

        Args:
            strategy: Filter by strategy name (optional).
            asset: Filter by asset ticker (optional).

        Returns:
            List of trade dicts.
        """
        query = "SELECT * FROM trades WHERE outcome IS NULL"
        params: list[Any] = []
        idx = 0

        if strategy:
            idx += 1
            query += f" AND strategy=${idx}"
            params.append(strategy)
        if asset:
            idx += 1
            query += f" AND asset=${idx}"
            params.append(asset)

        rows = await self.pool.fetch(query, *params)
        return self._rows(rows)

    async def get_rolling_stats(
        self,
        strategy: str,
        asset: str,
        n: int = 50,
    ) -> dict:
        """Return rolling win-rate statistics over the last ``n`` resolved trades.

        Args:
            strategy: Strategy name.
            asset: Asset ticker.
            n: Rolling window size.

        Returns:
            Dict with keys ``resolved``, ``win_rate``, ``avg_entry``.
        """
        rows = await self.pool.fetch(
            "SELECT outcome, entry_price FROM trades "
            "WHERE strategy=$1 AND asset=$2 AND outcome IS NOT NULL "
            "ORDER BY id DESC LIMIT $3",
            strategy, asset, n,
        )
        if not rows:
            return {"resolved": 0, "win_rate": 0.0, "avg_entry": 0.5}
        wins = sum(1 for r in rows if r["outcome"] == "WIN")
        total = len(rows)
        return {
            "resolved": total,
            "win_rate": wins / total,
            "avg_entry": sum(r["entry_price"] for r in rows) / total,
        }

    async def get_recent_outcomes(
        self,
        strategy: str,
        asset: str,
        n: int = 5,
    ) -> list[str]:
        """Last N outcomes for streak detection.

        Args:
            strategy: Strategy name.
            asset: Asset ticker.
            n: Number of recent outcomes.

        Returns:
            List of outcome strings (``"WIN"`` / ``"LOSS"``).
        """
        rows = await self.pool.fetch(
            "SELECT outcome FROM trades "
            "WHERE strategy=$1 AND asset=$2 AND outcome IS NOT NULL "
            "ORDER BY id DESC LIMIT $3",
            strategy, asset, n,
        )
        return [r["outcome"] for r in rows]

    async def get_stats(self, strategy: str, asset: str) -> dict:
        """Aggregate statistics for a strategy/asset pair.

        Args:
            strategy: Strategy name.
            asset: Asset ticker.

        Returns:
            Dict with keys ``trades``, ``open``, ``wins``, ``win_rate``,
            ``avg_entry``, ``total_pnl``, ``edge``.
        """
        rows = await self.pool.fetch(
            "SELECT outcome, entry_price, bet_size, pnl FROM trades "
            "WHERE strategy=$1 AND asset=$2 AND outcome IS NOT NULL",
            strategy, asset,
        )

        open_row = await self.pool.fetchrow(
            "SELECT COUNT(*) as n FROM trades "
            "WHERE strategy=$1 AND asset=$2 AND outcome IS NULL",
            strategy, asset,
        )
        open_count = open_row["n"] if open_row else 0

        if not rows:
            return {
                "trades": 0, "open": open_count, "win_rate": 0.0,
                "avg_entry": 0.0, "total_pnl": 0.0, "edge": 0.0, "wins": 0,
            }
        wins = sum(1 for r in rows if r["outcome"] == "WIN")
        total = len(rows)
        avg_entry = sum(r["entry_price"] for r in rows) / total
        total_pnl = sum(r["pnl"] for r in rows if r["pnl"] is not None)
        win_rate = wins / total
        return {
            "trades": total,
            "open": open_count,
            "wins": wins,
            "win_rate": round(win_rate * 100, 1),
            "avg_entry": round(avg_entry, 3),
            "total_pnl": round(total_pnl, 2),
            "edge": round(win_rate - avg_entry, 4),
        }

    async def get_all_stats(
        self,
        strategies: list[str],
        assets: list[str],
    ) -> list[dict]:
        """Stats for every (strategy, asset) combination.

        Args:
            strategies: List of strategy names.
            assets: List of asset tickers.

        Returns:
            List of stat dicts, one per combination.
        """
        results = []
        for strat in strategies:
            for asset in assets:
                stats = await self.get_stats(strat, asset)
                bankroll = await self.get_bankroll(strat, asset)
                results.append({
                    "strategy": strat,
                    "asset": asset,
                    "bankroll": round(bankroll, 2),
                    **stats,
                })
        return results

    # ─── Signal State ───────────────────────────────────────

    async def save_signal_state(
        self,
        strategy: str,
        asset: str,
        signal: str,
        confidence: float,
        snapshot: dict,
        rsi: float | None,
        bb_pct: float | None,
        regime: str,
        market_info: dict,
    ) -> None:
        """Upsert the latest signal state for a strategy/asset pair.

        Args:
            strategy: Strategy name.
            asset: Asset ticker.
            signal: Current signal value.
            confidence: Model confidence.
            snapshot: Feed snapshot dict.
            rsi: RSI-14 value.
            bb_pct: Bollinger %B value.
            regime: Market regime label.
            market_info: Dict with ``title`` and ``up_price`` keys.
        """
        await self.pool.execute(
            """INSERT INTO signal_state
              (strategy, asset, signal, confidence, price, cvd, vwap_change,
               funding_rate, liq_long, liq_short, rsi, bb_pct, regime,
               market_title, market_up_price, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (strategy, asset) DO UPDATE SET
              signal=EXCLUDED.signal, confidence=EXCLUDED.confidence,
              price=EXCLUDED.price, cvd=EXCLUDED.cvd,
              vwap_change=EXCLUDED.vwap_change, funding_rate=EXCLUDED.funding_rate,
              liq_long=EXCLUDED.liq_long, liq_short=EXCLUDED.liq_short,
              rsi=EXCLUDED.rsi, bb_pct=EXCLUDED.bb_pct, regime=EXCLUDED.regime,
              market_title=EXCLUDED.market_title, market_up_price=EXCLUDED.market_up_price,
              updated_at=EXCLUDED.updated_at
            """,
            strategy, asset, signal, confidence,
            snapshot.get("last_price"), snapshot.get("cvd_2min"),
            snapshot.get("vwap_change"), snapshot.get("funding_rate"),
            snapshot.get("liq_long_2min"), snapshot.get("liq_short_2min"),
            rsi, bb_pct, regime,
            market_info.get("title"), market_info.get("up_price"),
            self._now(),
        )

    async def get_signal_states(self) -> list[dict]:
        """Return all signal state rows ordered by strategy and asset.

        Returns:
            List of signal state dicts.
        """
        rows = await self.pool.fetch(
            "SELECT * FROM signal_state ORDER BY strategy, asset"
        )
        return self._rows(rows)

    # ─── Price History ──────────────────────────────────────

    async def record_price(self, asset: str, price: float) -> None:
        """Insert a price observation and prune to 200 rows per asset.

        Args:
            asset: Asset ticker.
            price: Observed price.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO price_history (timestamp, asset, price) "
                    "VALUES ($1, $2, $3)",
                    self._now(), asset, price,
                )
                await conn.execute(
                    "DELETE FROM price_history WHERE asset=$1 AND id NOT IN "
                    "(SELECT id FROM price_history WHERE asset=$1 "
                    "ORDER BY id DESC LIMIT 200)",
                    asset,
                )

    async def get_price_history(self, asset: str, limit: int = 150) -> list[dict]:
        """Return recent price observations in chronological order.

        Args:
            asset: Asset ticker.
            limit: Maximum rows to return.

        Returns:
            List of dicts with ``timestamp`` and ``price`` keys, oldest first.
        """
        rows = await self.pool.fetch(
            "SELECT timestamp, price FROM price_history "
            "WHERE asset=$1 ORDER BY id DESC LIMIT $2",
            asset, limit,
        )
        return list(reversed(self._rows(rows)))

    # ─── Risk Events ────────────────────────────────────────

    async def log_risk_event(
        self,
        event_type: str,
        strategy: str | None,
        asset: str | None,
        details: str,
    ) -> None:
        """Record a risk management event.

        Args:
            event_type: Event category (e.g. ``"DRAWDOWN"``, ``"CIRCUIT_BREAK"``).
            strategy: Affected strategy (may be ``None`` for portfolio-level).
            asset: Affected asset (may be ``None``).
            details: Human-readable description.
        """
        await self.pool.execute(
            "INSERT INTO risk_events (timestamp, event_type, strategy, asset, details) "
            "VALUES ($1, $2, $3, $4, $5)",
            self._now(), event_type, strategy, asset, details,
        )

    async def get_recent_risk_events(self, limit: int = 50) -> list[dict]:
        """Return the most recent risk events.

        Args:
            limit: Maximum rows to return.

        Returns:
            List of risk event dicts, newest first.
        """
        rows = await self.pool.fetch(
            "SELECT * FROM risk_events ORDER BY id DESC LIMIT $1",
            limit,
        )
        return self._rows(rows)

    # ─── Regime History ─────────────────────────────────────

    async def save_regime(
        self,
        asset: str,
        regime: str,
        adx: float | None,
        bb_width: float | None,
        ema_slope: float | None,
    ) -> None:
        """Insert a regime observation.

        Args:
            asset: Asset ticker.
            regime: Regime label (``"TRENDING"``, ``"RANGING"``, etc.).
            adx: Average Directional Index value.
            bb_width: Bollinger Band width.
            ema_slope: EMA slope value.
        """
        await self.pool.execute(
            "INSERT INTO regime_history "
            "(timestamp, asset, regime, adx, bb_width, ema_slope) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            self._now(), asset, regime, adx, bb_width, ema_slope,
        )

    # ─── Recent Trades ──────────────────────────────────────

    async def get_recent_trades(self, limit: int = 100) -> list[dict]:
        """Return the most recent trades across all strategies.

        Args:
            limit: Maximum rows to return.

        Returns:
            List of trade dicts, newest first.
        """
        rows = await self.pool.fetch(
            "SELECT * FROM trades ORDER BY id DESC LIMIT $1",
            limit,
        )
        return self._rows(rows)

    async def get_trades_for_strategy(
        self,
        strategy: str,
        limit: int = 500,
    ) -> list[dict]:
        """Return recent trades for a single strategy.

        Args:
            strategy: Strategy name.
            limit: Maximum rows to return.

        Returns:
            List of trade dicts, newest first.
        """
        rows = await self.pool.fetch(
            "SELECT * FROM trades WHERE strategy=$1 ORDER BY id DESC LIMIT $2",
            strategy, limit,
        )
        return self._rows(rows)

    # ─── Active positions count per direction ───────────────

    async def count_open_by_direction(self, asset: str) -> dict[str, int]:
        """Count open trades per signal direction for an asset.

        Args:
            asset: Asset ticker.

        Returns:
            Dict mapping signal direction to count,
            e.g. ``{"BUY_YES": 3, "BUY_NO": 1}``.
        """
        result: dict[str, int] = {"BUY_YES": 0, "BUY_NO": 0}
        rows = await self.pool.fetch(
            "SELECT signal, COUNT(*) as n FROM trades "
            "WHERE asset=$1 AND outcome IS NULL GROUP BY signal",
            asset,
        )
        for row in rows:
            result[row["signal"]] = row["n"]
        return result
