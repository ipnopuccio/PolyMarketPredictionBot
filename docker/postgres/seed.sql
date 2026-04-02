-- =============================================================================
-- seed.sql — btc-bot-v2 initial bankroll seed
-- =============================================================================
-- This file runs on the FIRST `docker compose up` after init.sql, via:
--
--   volumes:
--     - ./docker/postgres/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql
--
-- PURPOSE: Insert the starting $40 bankroll for all 5 active bots so the
-- executor has capital to allocate on first run.
--
-- This file is safe to re-run: INSERT ... ON CONFLICT DO NOTHING means
-- rows are only inserted if they do not already exist.  If you restore a
-- database backup, the existing bankroll rows will be preserved.
--
-- Active bots (from CLAUDE.md — never re-add RSI_HUNTER / LIQ_HUNTER):
--   1. TURBO_CVD  / ETH
--   2. TURBO_VWAP / ETH
--   3. MOMENTUM   / BTC
--   4. MOMENTUM   / SOL
--   5. BOLLINGER  / BTC
--
-- Initial bankroll per bot: $40 USD (matches settings.initial_bankroll)
-- =============================================================================

INSERT INTO bankroll (strategy, asset, current, peak, updated_at)
VALUES
    ('TURBO_CVD',  'ETH', 40.0, 40.0, NOW()),
    ('TURBO_VWAP', 'ETH', 40.0, 40.0, NOW()),
    ('MOMENTUM',   'BTC', 40.0, 40.0, NOW()),
    ('MOMENTUM',   'SOL', 40.0, 40.0, NOW()),
    ('BOLLINGER',  'BTC', 40.0, 40.0, NOW())
ON CONFLICT (strategy, asset) DO NOTHING;
