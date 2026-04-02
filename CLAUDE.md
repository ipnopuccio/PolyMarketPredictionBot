# CLAUDE.md — btc-bot-v2 (Polymarket Up/Down Trading Bot)

> Single source of truth. Auto-generated 2026-03-26 via `/init`.

---

## Project Overview

**Name:** btc-bot-v2
**Version:** 2.0.0
**Status:** Paper Trading (MODE=paper)
**Goal:** Multi-strategy Polymarket Up/Down bot — only profitable strategies from v1 data.

The bot watches Binance Futures feeds (BTC/ETH/SOL), generates directional signals,
and places paper trades on Polymarket Up/Down binary markets.

---

## Tech Stack

| Layer | Technology | Version | Notes |
|-------|-----------|---------|-------|
| Language | Python | >=3.11 | Fully async (asyncio) |
| Config | Pydantic v2 + pydantic-settings | >=2.5 | Typed, env-based |
| HTTP | httpx | >=0.27 | Async client |
| WebSocket | websockets | >=12.0 | Binance feed |
| Database | aiosqlite / asyncpg | >=0.20 / >=0.29 | SQLite (local) or PostgreSQL (Docker) |
| API/Dashboard | FastAPI + Uvicorn | >=0.109 | Port 5003, dark theme |
| Templates | Jinja2 | >=3.1 | Server-side dashboard |
| Math | numpy | >=1.26 | RSI, Bollinger, stats |
| Metrics | prometheus-client | >=0.20 | /metrics endpoint |
| Logging | python-json-logger | >=2.0 | Structured JSON (Docker) |
| Testing | pytest + pytest-asyncio | dev dep | 478 tests passing |

---

## Architecture

```
                    ┌─────────────────────────────────┐
                    │         MAIN ORCHESTRATOR        │
                    │         (src/bot/main.py)        │
                    └─────┬───────────────────┬───────┘
                          │                   │
              ┌───────────▼──────┐   ┌───────▼────────┐
              │  BinanceFeed     │   │  RSIFeed        │
              │  (WS + REST)     │   │  (1min candles) │
              │  BTC/ETH/SOL     │   │  RSI-14 + BB    │
              └───────────┬──────┘   └───────┬────────┘
                          │ FeedSnapshot      │ RSI/BB
                          ▼                   ▼
              ┌──────────────────────────────────────┐
              │        5 × STRATEGY BOT LOOPS        │
              │  TurboCvd(ETH), TurboVwap(ETH),      │
              │  Momentum(BTC), Momentum(SOL),        │
              │  Bollinger(BTC)                       │
              └───────────────┬──────────────────────┘
                              │ SignalResult
                              ▼
              ┌──────────────────────────────────────┐
              │           EXECUTOR                    │
              │  VPN check → Bankroll → Window cap →  │
              │  Elapsed gate → Risk → Market find →  │
              │  Idempotency → Entry price → ORDER    │
              └───────────────┬──────────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │  Polymarket CLOB   │
                    │  (paper mode)      │
                    └─────────┬──────────┘
                              │
              ┌───────────────▼──────────────────────┐
              │         RESOLVER (30s poll)           │
              │  Gamma API → resolve open trades →   │
              │  update outcome/PnL → recalc bankroll │
              └──────────────────────────────────────┘

              ┌──────────────────────────────────────┐
              │         DASHBOARD (FastAPI:5003)      │
              │  /api/v2/positions, /pnl, /params,   │
              │  /logs, /health + HTML overview       │
              │  Auth: X-API-Key header               │
              └──────────────────────────────────────┘
```

---

## Active Bots (v1-validated profitable strategies only)

| # | Strategy | Asset | v1 Win Rate | v1 P&L | Interval | Max Orders/Window |
|---|----------|-------|-------------|--------|----------|-------------------|
| 1 | TURBO_CVD | ETH | 88.0% | +$47.92 | 6s | 30 |
| 2 | TURBO_VWAP | ETH | 83.8% | +$19.78 | 6s | 30 |
| 3 | MOMENTUM | BTC | 96.4% | +$12.80 | 15s | 10 |
| 4 | MOMENTUM | SOL | 100% | +$6.28 | 15s | 10 |
| 5 | BOLLINGER | BTC | 100% | +$0.49 | 15s | 5 |

**Never re-add:** RSI_HUNTER, LIQ_HUNTER, REVERSAL (unprofitable in v1).

---

## File Structure (current)

```
btc-bot-v2/
├── src/bot/
│   ├── __init__.py
│   ├── __main__.py          # Entry: python -m bot
│   ├── main.py              # Orchestrator (272 lines)
│   ├── config.py            # Pydantic settings (116 lines)
│   ├── core/
│   │   ├── events.py        # EventBus pub/sub (33 lines)
│   │   └── types.py         # Enums, dataclasses (103 lines)
│   ├── strategies/
│   │   ├── base.py          # Abstract BaseStrategy (62 lines)
│   │   ├── momentum.py      # CVD + VWAP trend (53 lines)
│   │   ├── bollinger.py     # BB breakout (53 lines)
│   │   ├── turbo_cvd.py     # Pure CVD pressure (42 lines)
│   │   └── turbo_vwap.py    # Pure VWAP deviation (42 lines)
│   ├── execution/
│   │   ├── executor.py      # Unified executor + WindowTracker
│   │   ├── sizer.py         # 1/3 fractional Kelly
│   │   ├── risk.py          # Drawdown, correlation, circuit breaker
│   │   └── resolver.py      # Gamma API resolution loop
│   ├── feeds/
│   │   ├── binance_ws.py    # Multi-asset WS + REST polling
│   │   ├── rsi_feed.py      # RSI-14 + Bollinger calculator
│   │   ├── exchange_adapter.py  # Abstract base + NormalizedTick, ExchangeHealth
│   │   ├── exchange_manager.py  # Multi-exchange orchestrator (median, outliers)
│   │   └── adapters/
│   │       ├── binance.py       # Primary adapter (full indicators)
│   │       ├── ccxt_adapter.py  # Secondary CEX adapter (WS + REST fallback)
│   │       └── dexscreener.py   # DEX aggregator via DexScreener API
│   ├── market/
│   │   ├── finder.py        # Polymarket market discovery
│   │   └── orderbook.py     # CLOB orderbook (5s cache)
│   ├── storage/
│   │   ├── database.py      # Async SQLite (429 lines, 6 tables)
│   │   ├── postgres.py      # Async PostgreSQL via asyncpg (747 lines)
│   │   └── factory.py       # DB adapter factory (SQLite vs PostgreSQL)
│   ├── backtest/
│   │   ├── models.py        # Pydantic types: BacktestConfig, SimulatedTrade, etc.
│   │   ├── metrics.py       # Sharpe, Sortino, Max DD, Win Rate, Profit Factor
│   │   ├── data_provider.py # Synthetic GBM snapshots + DB loader
│   │   ├── engine.py        # Core backtester: replay strategies, simulate P&L
│   │   ├── walk_forward.py  # Walk-forward IS/OOS analysis (5 windows)
│   │   ├── monte_carlo.py   # Trade-order randomization (1000 iterations)
│   │   └── report.py        # Self-contained HTML report + Chart.js
│   ├── dashboard/
│   │   ├── app.py           # FastAPI + CORS + security middleware
│   │   ├── server.py        # REST API v2 endpoints + /exchanges health
│   │   ├── backtest_api.py  # POST /api/v2/backtest + /report
│   │   ├── auth.py          # Dual API key auth (primary + secondary rotation)
│   │   ├── security.py      # Rate limiter + body size + headers + audit log
│   │   ├── log_buffer.py    # Ring-buffer 500 entries
│   │   ├── ws_broker.py     # In-memory pub/sub (4 channels, 1000 msg/client)
│   │   ├── ws_stream.py     # WS + SSE endpoints (/api/v1/stream, API key auth)
│   │   └── ws_bridge.py     # EventBus → WSBroker bridge (5 handlers)
│   ├── monitoring/
│   │   ├── metrics.py       # Prometheus counters/gauges/histograms
│   │   └── logging_config.py # Structured JSON logging setup
│   └── network/
│       └── vpn_guard.py     # VPN tunnel detection
├── docker/
│   ├── bot.Dockerfile       # Multi-stage Python 3.11-slim
│   ├── dashboard.Dockerfile # Node 20 build → nginx:alpine
│   ├── dashboard-nginx.conf # SPA routing for React dashboard
│   ├── nginx.conf           # Reverse proxy, rate limiting, WebSocket
│   ├── ssl/.gitkeep         # SSL cert mount point
│   └── postgres/
│       ├── init.sql          # PostgreSQL schema (6 tables + indexes)
│       └── seed.sql          # Initial bankroll for 5 active bots
├── scripts/
│   ├── healthcheck.sh       # Service health check
│   └── backup.sh            # PostgreSQL backup + 30-day pruning
├── docker-compose.yml       # bot + dashboard + postgres + nginx + prometheus
├── tests/                   # pytest suite (409 passing)
├── pyproject.toml           # Dependencies + pytest config
├── .env.example             # Template (local + Docker vars)
├── .env                     # Active config (MODE=paper)
├── btc_bot_v2.db            # SQLite database (164KB)
└── CLAUDE.md                # This file
```

---

## Database Schema (SQLite WAL / PostgreSQL)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `trades` | Core trade log | strategy, asset, market_id, signal, entry_price, bet_size, confidence, outcome, pnl |
| `bankroll` | Per-strategy/asset capital | PK(strategy, asset), current, peak |
| `signal_state` | Latest indicators per bot | PK(strategy, asset), all indicator snapshots |
| `price_history` | Last 200 prices/asset | timestamp, asset, price (60s intervals) |
| `risk_events` | Audit trail | event_type, strategy, asset, details |
| `regime_history` | Market regime tracking | asset, regime, adx, bb_width, ema_slope |

---

## API Endpoints (current)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | HTML dashboard (dark theme) |
| GET | `/api/overview` | No | Aggregated PnL/stats |
| GET | `/api/v2/positions` | API Key | Open trades |
| GET | `/api/v2/pnl` | API Key | P&L by strategy/asset |
| GET | `/api/v2/params` | API Key | Current strategy parameters |
| POST | `/api/v2/params` | API Key | Hot-update params (no restart) |
| GET | `/api/v2/logs` | API Key | Recent log entries |
| GET | `/api/v2/health` | API Key | Bot/VPN/DB/Feed health |
| GET | `/metrics` | No | Prometheus scrape endpoint |
| GET | `/api/v2/exchanges` | API Key | Per-exchange health (latency, errors) |
| POST | `/api/v2/backtest` | API Key | Run backtest (JSON result) |
| POST | `/api/v2/backtest/report` | API Key | Run backtest (HTML report) |
| WS | `/api/v1/stream` | API Key | Real-time WS (prices, signals, trades, metrics) |
| GET | `/api/v1/stream` | API Key | SSE fallback for real-time data |

---

## External Integrations

| Service | Protocol | Purpose |
|---------|----------|---------|
| Binance Futures | WebSocket | aggTrade, markPrice, forceOrder, bookTicker (PRIMARY) |
| Binance Futures | REST (30s) | Open interest, long/short ratio |
| Coinbase, Kraken, Bybit, OKX | CCXT WS/REST | Secondary price consensus (CEX) |
| DexScreener | REST (10s) | DEX price aggregator (80+ DEXes) |
| Polymarket Gamma | REST (30s) | Market discovery + resolution |
| Polymarket CLOB | REST (5s cache) | Orderbook / best ask price |

---

## Risk Management

- **Kelly Sizer:** 1/3 fractional Kelly, min 1% / max 10% of bankroll, 50-trade rolling window
- **Drawdown:** Strategy disabled at -25%, portfolio paused at -20% (30 min)
- **Correlation:** Max 2 same-direction per asset, max 60% unidirectional exposure
- **Circuit Breaker:** 5 consecutive losses in 30-trade window → pause 10 windows
- **VPN Guard:** Blocks all orders without active VPN tunnel
- **Entry Guards:** Strategy-specific max entry price validation

---

## How to Run

```bash
# ── Local (SQLite) ──────────────────────────────────
cd btc-bot-v2
python -m bot              # Run all 5 bots + dashboard
python -m bot --status     # Print leaderboard table
# Dashboard: http://localhost:5003

# ── Docker (PostgreSQL) ─────────────────────────────
cp .env.example .env && nano .env   # Set DB_PASSWORD + API_KEY
docker compose up -d                 # Start all services
docker compose logs -f bot           # Watch bot logs
./scripts/healthcheck.sh             # Verify all services
./scripts/backup.sh                  # Backup PostgreSQL
```

---

## Configuration

All config via `src/bot/config.py` (Pydantic Settings) + `.env`:

```
MODE=paper              # Only mode currently
DB_PATH=btc_bot_v2.db
DASHBOARD_PORT=5003
API_KEY=<auto-generated>
VPN_CHECK=auto
```

Strategy params are typed per-strategy (MomentumConfig, BollingerConfig, etc.)
and can be hot-updated via `POST /api/v2/params`.

---

## Development Commands

```bash
# Run bot
python -m bot

# Run tests (140+ passing)
pytest tests/ -v --asyncio-mode=auto

# Check types (when configured)
mypy src/

# Lint (when configured)
ruff check src/
```

---

## Constraints

- **Paper trading only** — no live execution path exists
- **No secrets in code** — API keys via .env / env vars
- **No global state** — Pydantic Settings singleton, async everywhere
- **asyncio only** — no threading
- **Only profitable strategies** — never re-add RSI_HUNTER, LIQ_HUNTER, REVERSAL

---

## Workflow Rules (OBBLIGATORIO)

### Auto-Commit dopo ogni modifica
**Ogni volta che viene scritto o modificato codice, DEVI fare commit e push su GitHub.**

Procedura:
1. Completare la modifica (file nuovi o editati)
2. `git add` dei file modificati (mai `.env`, `*.db`, `__pycache__/`)
3. `git commit` con messaggio dettagliato che spiega:
   - **Cosa** è stato fatto (quali file, quali componenti)
   - **Perché** è stato fatto (quale fase della roadmap, quale bug, quale feature)
   - **Cosa cambia** per l'utente/sistema (nuovi endpoint, nuovi test, nuovi comportamenti)
4. `git push origin main`
5. Confermare all'utente con il link al commit

Formato commit message:
```
[Phase X.Y] Titolo breve della modifica

Dettaglio:
- File creati/modificati e perché
- Nuovi test aggiunti (se applicabile)
- Breaking changes (se applicabile)
- Note tecniche rilevanti

Co-Authored-By: Claude <noreply@anthropic.com>
```

**MAI** committare: `.env`, `*.db`, `node_modules/`, `__pycache__/`, file con secrets.

---

## Reference Documents

The following strategic documents in `../files/` define the target architecture:

| Document | Purpose |
|----------|---------|
| `QUICK_REFERENCE.md` | Cheat sheet — 10 commands, stack, endpoints |
| `CLAUDE_CODE_PROMPT.md` | Master prompt — command specs & quality criteria |
| `CLAUDE_CODE_MASTER_PROMPT.md` | Deep dive — architecture, schemas, deployment |
| `OPTIMIZATION_CHECKLIST.md` | 30-day roadmap — 10 phases with verification |
| `STRATEGIC_QUESTIONS.md` | 28 questions guiding decisions |
| `README_START_HERE.md` | How to use the document system |

---

## Development Roadmap

| Phase | Name | Status | Date |
|-------|------|--------|------|
| 0 | Setup | DONE | 2026-03-24 |
| 1 | Init/Audit | DONE | 2026-03-26 |
| 2 | Multi-Exchange | DONE | 2026-03-28 |
| 3 | WebSocket API | DONE | 2026-03-28 |
| 4 | React Dashboard | PARTIAL | 2026-03-26 |
| 5 | Backtest Suite | DONE | 2026-03-28 |
| 6 | VPS Deployment | DONE | 2026-03-28 |
| 7 | Monitoring | DONE | 2026-03-28 |
| 8 | Security | DONE | 2026-03-28 |
| 9 | Strategy Eval | DONE | 2026-03-28 |
| 10 | Optimization | DONE | 2026-03-31 |
