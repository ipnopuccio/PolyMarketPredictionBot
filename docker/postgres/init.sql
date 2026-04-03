-- =============================================================================
-- init.sql — btc-bot-v2 PostgreSQL schema
-- =============================================================================
-- This file runs automatically on the FIRST `docker compose up` when the
-- postgres_data volume is empty.  It is mounted as an init script via:
--
--   volumes:
--     - ./docker/postgres/init.sql:/docker-entrypoint-initdb.d/01-init.sql
--
-- Mirrors the SQLite schema in src/bot/storage/database.py exactly, with
-- PostgreSQL-appropriate types:
--   - SERIAL              replaces INTEGER PRIMARY KEY (auto-increment)
--   - TIMESTAMPTZ         replaces TEXT for timestamps (timezone-aware)
--   - DOUBLE PRECISION    replaces REAL (64-bit float, same as Python float)
--   - TEXT                replaces TEXT (unchanged)
--   - ON CONFLICT DO UPDATE becomes INSERT ... ON CONFLICT for upserts
--
-- Partial indexes (WHERE outcome IS NULL) are supported natively in
-- PostgreSQL, so idx_trades_outcome_null is kept as-is.
-- =============================================================================

-- =============================================================================
-- Table: trades
-- Core trade log — one row per Polymarket order placed by a strategy bot.
-- =============================================================================
CREATE TABLE IF NOT EXISTS trades (
    id                      SERIAL PRIMARY KEY,
    timestamp               TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    strategy                TEXT                NOT NULL,
    asset                   TEXT                NOT NULL,
    market_id               TEXT,
    signal                  TEXT                NOT NULL,
    entry_price             DOUBLE PRECISION    NOT NULL,
    bet_size                DOUBLE PRECISION    NOT NULL,
    confidence              DOUBLE PRECISION    NOT NULL DEFAULT 0.5,
    regime                  TEXT                NOT NULL DEFAULT 'UNKNOWN',
    outcome                 TEXT,                -- NULL = open, 'WIN' or 'LOSS' when resolved
    pnl                     DOUBLE PRECISION,    -- NULL until resolved
    -- Indicator snapshots at time of signal
    cvd_at_signal           DOUBLE PRECISION,
    funding_at_signal       DOUBLE PRECISION,
    liq_long_at_signal      DOUBLE PRECISION,
    liq_short_at_signal     DOUBLE PRECISION,
    vwap_change_at_signal   DOUBLE PRECISION,
    rsi_at_signal           DOUBLE PRECISION,
    bb_pct_at_signal        DOUBLE PRECISION,
    indicators_json         JSONB               -- Full indicator snapshot (Phase 11.3)
);

-- Composite index: used by get_stats() and get_rolling_stats() per (strategy, asset)
CREATE INDEX IF NOT EXISTS idx_trades_strategy_asset_outcome
    ON trades (strategy, asset, outcome);

-- Index: used by resolver to look up trades by Polymarket market_id
CREATE INDEX IF NOT EXISTS idx_trades_market_id
    ON trades (market_id);

-- Partial index: used by get_open_trades() — only scans unresolved rows
CREATE INDEX IF NOT EXISTS idx_trades_outcome_null
    ON trades (outcome)
    WHERE outcome IS NULL;

-- Covering index: optimises the rolling-stats ORDER BY id DESC LIMIT N query
CREATE INDEX IF NOT EXISTS idx_trades_strategy_asset_id
    ON trades (strategy, asset, id DESC);

-- =============================================================================
-- Table: bankroll
-- Per-strategy/asset capital tracking. Composite PK (strategy, asset).
-- =============================================================================
CREATE TABLE IF NOT EXISTS bankroll (
    strategy    TEXT                NOT NULL,
    asset       TEXT                NOT NULL,
    current     DOUBLE PRECISION    NOT NULL,
    peak        DOUBLE PRECISION    NOT NULL,
    updated_at  TIMESTAMPTZ         NOT NULL DEFAULT NOW(),
    PRIMARY KEY (strategy, asset)
);

-- =============================================================================
-- Table: signal_state
-- Latest indicator snapshot per active bot. Upserted on every signal cycle.
-- Composite PK (strategy, asset) — one row per bot, always current.
-- =============================================================================
CREATE TABLE IF NOT EXISTS signal_state (
    strategy        TEXT                NOT NULL,
    asset           TEXT                NOT NULL,
    signal          TEXT,
    confidence      DOUBLE PRECISION,
    price           DOUBLE PRECISION,
    cvd             DOUBLE PRECISION,
    vwap_change     DOUBLE PRECISION,
    funding_rate    DOUBLE PRECISION,
    liq_long        DOUBLE PRECISION,
    liq_short       DOUBLE PRECISION,
    rsi             DOUBLE PRECISION,
    bb_pct          DOUBLE PRECISION,
    regime          TEXT,
    market_title    TEXT,
    market_up_price DOUBLE PRECISION,
    updated_at      TIMESTAMPTZ,
    PRIMARY KEY (strategy, asset)
);

-- =============================================================================
-- Table: price_history
-- Last 200 prices per asset (trimmed on insert, 60-second granularity).
-- =============================================================================
CREATE TABLE IF NOT EXISTS price_history (
    id          SERIAL      PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    asset       TEXT        NOT NULL,
    price       DOUBLE PRECISION NOT NULL
);

-- Index: used by get_price_history() ORDER BY id DESC LIMIT 200 WHERE asset=?
CREATE INDEX IF NOT EXISTS idx_price_history_asset_id
    ON price_history (asset, id DESC);

-- =============================================================================
-- Table: risk_events
-- Immutable audit trail for drawdown triggers, circuit breakers, VPN events.
-- =============================================================================
CREATE TABLE IF NOT EXISTS risk_events (
    id          SERIAL      PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type  TEXT        NOT NULL,
    strategy    TEXT,        -- NULL for portfolio-level events
    asset       TEXT,
    details     TEXT
);

-- Index: used by get_recent_risk_events() ORDER BY id DESC LIMIT N
CREATE INDEX IF NOT EXISTS idx_risk_events_timestamp
    ON risk_events (id DESC);

-- =============================================================================
-- Table: regime_history
-- Market regime time-series (TRENDING / RANGING / VOLATILE / QUIET).
-- =============================================================================
CREATE TABLE IF NOT EXISTS regime_history (
    id          SERIAL      PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    asset       TEXT        NOT NULL,
    regime      TEXT        NOT NULL,
    adx         DOUBLE PRECISION,
    bb_width    DOUBLE PRECISION,
    ema_slope   DOUBLE PRECISION
);

-- Index: used by regime lookups per asset ordered by recency
CREATE INDEX IF NOT EXISTS idx_regime_history_asset_id
    ON regime_history (asset, id DESC);

-- =============================================================================
-- Table: equity_snapshots (Phase 11.2)
-- Periodic bankroll snapshots for equity curve charting.
-- =============================================================================
CREATE TABLE IF NOT EXISTS equity_snapshots (
    id          SERIAL              PRIMARY KEY,
    timestamp   DOUBLE PRECISION    NOT NULL,
    strategy    TEXT                NOT NULL,
    asset       TEXT                NOT NULL,
    bankroll    DOUBLE PRECISION    NOT NULL,
    total_pnl   DOUBLE PRECISION    NOT NULL,
    open_trades INTEGER             NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ         DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_equity_ts
    ON equity_snapshots (strategy, asset, timestamp);
