# ROADMAP — btc-bot-v2 Development Plan

> Documento vivente. Aggiornato step-by-step ad ogni sessione.
> Ultimo aggiornamento: **2026-03-31**

---

## Status Generale

| Fase | Nome | Status | Test | Note |
|------|------|--------|------|------|
| 0 | Setup Iniziale | DONE | - | /init, CLAUDE.md, 6 docs letti |
| F | Foundation (Test Suite) | DONE | 180 | 13 file, 0.57s, tutti verdi |
| 2 | Multi-Exchange (CCXT) | **DONE** | 58 | CEX (coinbase/kraken/bybit/okx) + DEX (DexScreener) + WS mode |
| 3 | WebSocket API | **DONE** | 31 | Broker + WS/SSE + auth + metrics channel |
| 4 | React Dashboard | **DONE** | 0 | Vite + React 19 + TS + TailwindCSS v4 |
| 5 | Backtest Suite | **DONE** | 140 | Engine + walk-forward + Monte Carlo + HTML reports |
| 6 | VPS Deployment | **DONE** | 0 | Docker Compose + PostgreSQL + Nginx + healthcheck |
| 7 | Monitoring & Observability | **DONE** | 0 | Prometheus + JSON logging + 12 alert rules |
| 8 | Security Hardening | **DONE** | 0 | CORS, rate limit, CSP, audit, key rotation |
| 9 | Strategy Evaluation | **DONE** | 29 | Evaluator + comparison + report + API + CSV |
| 10 | Optimization & Polish | **DONE** | 40 | Refactor, E2E, stress, load, OpenAPI |

**Totale test: 478 passing (18.55s)**

---

## Phase 0: Setup Iniziale — DONE (2026-03-26)

- [x] Letti e integrati 6 documenti strategici (`files/`)
- [x] Eseguito `/init` — audit completo codebase
- [x] Creato `CLAUDE.md` — single source of truth
- [x] Creato 9 agenti custom (`.claude/agents/`)
- [x] Installate skills specializzate
- [x] Creato sistema memory persistente

---

## Phase F: Foundation (Test Suite) — DONE (2026-03-26)

Target: >70% copertura moduli critici. Risultato: **180 test, 0 fail, 0.57s**.

### File creati
| File | Test | Copertura |
|------|------|-----------|
| `tests/conftest.py` | - | Fixtures condivise (db, feed_snapshot, configs, helpers) |
| `tests/test_strategies/test_momentum.py` | 17 | BUY_YES/NO/SKIP, confidence, entry_ok() |
| `tests/test_strategies/test_bollinger.py` | 10 | BB null skip, above/below, confidence=1.0 |
| `tests/test_strategies/test_turbo_cvd.py` | 7 | CVD threshold, confidence, skip |
| `tests/test_strategies/test_turbo_vwap.py` | 7 | VWAP threshold, confidence, skip |
| `tests/test_storage/test_database.py` | 22 | CRUD, atomicita', stats, rolling |
| `tests/test_feeds/test_rsi_feed.py` | 11 | RSI-14, BB calc, candle boundary |
| `tests/test_feeds/test_binance_ws.py` | 22 | State, snapshot, is_healthy |
| `tests/test_execution/test_sizer.py` | 9 | Kelly, warmup, clamp min/max |
| `tests/test_execution/test_risk.py` | 14 | Drawdown, correlation, circuit breaker |
| `tests/test_execution/test_executor.py` | 14 | 12 pre-check, successful trade |
| `tests/test_execution/test_resolver.py` | 15 | PnL calc, infer resolution, cycle |
| `tests/test_dashboard/test_api.py` | 10 | HTML, auth, positions, PnL, health |
| **Totale** | **180** | **Tutti verdi** |

---

## Phase 2: Multi-Exchange Integration — DONE (2026-03-28)

### Architettura
- **PRIMARY:** BinanceAdapter (WS — full indicators: CVD, funding, liquidations, book imbalance)
- **SECONDARY CEX:** coinbase, kraken, bybit, okx via CCXTAdapter
  - WS mode (`watch_ticker`) con fallback automatico a REST polling 5s
  - Symbol mapping per exchange (USD vs USDT)
- **SECONDARY DEX:** DexScreenerAdapter (80+ DEX aggregati, REST 10s)
- **ExchangeManager:** median price consensus + MAD outlier detection (3-sigma)
- **Config default:** `secondary_exchanges=["coinbase","kraken","bybit","okx"]`, `dex_enabled=True`

### Completamenti sessione 2026-03-28
- [x] CCXTAdapter potenziato con `watch_ticker()` WebSocket + REST fallback
- [x] DexScreenerAdapter creato (`adapters/dexscreener.py`, 165 righe)
- [x] Config defaults attivati (4 CEX + DEX)
- [x] `GET /api/v2/exchanges` — health endpoint per-exchange
- [x] exchange_mgr passato a dashboard via app state
- [x] Prometheus metriche exchange (latency, up/down, price per exchange)

### Test: 58 (originali) + validazione 409 passing

---

## Phase 3: WebSocket API — DONE (2026-03-28)

### Componenti base (pre-esistenti)
- [x] WSBroker — In-memory pub/sub (4 canali, MAX_QUEUE=1000)
- [x] ws_stream.py — `ws://host:port/api/v1/stream` + SSE fallback
- [x] WSBridge — EventBus → Broker (5 event handlers)

### Completamenti sessione 2026-03-28
- [x] **API key auth su WebSocket** — `X-API-Key` header o `?api_key=` query param
- [x] **API key auth su SSE** — stessa validazione
- [x] **metrics.updated pubblicato** — dopo ogni trade resolution (resolver.py)
- [x] **Periodic metrics publisher** — ogni 30s pubblica total_pnl, bankroll, win_rate, trades
- [x] Prometheus metriche WS (clients connected, messages sent/dropped per channel)

### Test: 31 (originali) + validazione 409 passing

---

## Phase 4: React Dashboard — DONE (2026-03-26)

### Obiettivo
SPA React con dati real-time via WebSocket + REST polling fallback.

### Stack implementato
- **Vite 6** + **React 19** + **TypeScript** (strict mode)
- **TailwindCSS v4** (@tailwindcss/vite plugin, CSS custom properties dark theme)
- **Recharts** (BarChart P&L per strategia, celle verdi/rosse)
- **WebSocket hook** con auto-reconnect (exponential backoff 1s→30s)
- **useApi hook** generico per REST polling con intervallo configurabile

### File creati
| File | Descrizione |
|------|-------------|
| `dashboard/vite.config.ts` | Vite + React + TailwindCSS v4, proxy → localhost:5003 |
| `dashboard/tsconfig.json` | Project references (app + node) |
| `dashboard/tsconfig.app.json` | Strict, ES2023, react-jsx, bundler resolution |
| `dashboard/src/index.css` | Tailwind import + CSS custom properties (dark theme) |
| `dashboard/src/types/api.ts` | TS interfaces matching Python backend |
| `dashboard/src/hooks/useWebSocket.ts` | WS hook, 4 canali, exponential backoff, reconnect |
| `dashboard/src/hooks/useApi.ts` | REST polling generico con intervallo |
| `dashboard/src/components/TopBar.tsx` | Header: stats, WS dot, mode badge |
| `dashboard/src/components/BotCards.tsx` | Grid bot cards: signal arrows, confidence, stats |
| `dashboard/src/components/SignalTable.tsx` | Tabella segnali live (Price, CVD, VWAP%, Funding, RSI, BB%, Regime) |
| `dashboard/src/components/TradesTable.tsx` | Tabella trade recenti con outcome coloring |
| `dashboard/src/components/PnlChart.tsx` | Recharts BarChart P&L per strategia |
| `dashboard/src/components/HealthStatus.tsx` | System health con dot colorati |
| `dashboard/src/App.tsx` | Main app: 6 sezioni, useApi + useWebSocket |

### Sezioni dashboard
1. **TopBar** — Wallet, total P&L, win rate, active bots, WS status
2. **Active Bots** — Card grid con signal direction, confidence bar, stats
3. **Performance** — P&L bar chart per strategia (verde/rosso)
4. **Live Signals** — Tabella indicatori real-time (CVD, VWAP, funding, RSI, BB, regime)
5. **Recent Trades** — Tabella ultimi 50 trade (time, bot, signal, entry, size, outcome, P&L)
6. **Risk Events** — Log eventi rischio (se presenti)
7. **System Health** — Status componenti (DB, VPN, feeds)

### Build
- `npm run build` → `tsc -b && vite build` — **zero errori**
- Bundle: 544KB JS (164KB gzip), 11KB CSS (3KB gzip)

---

## Phase 5: Backtest Suite — DONE (2026-03-28)

### Componenti implementati
- [x] `src/bot/backtest/engine.py` — Core backtester (replay strategies, simulate P&L)
- [x] `src/bot/backtest/data_provider.py` — Synthetic GBM snapshots + DB loader
- [x] `src/bot/backtest/metrics.py` — Sharpe, Sortino, Max DD, Win Rate, Profit Factor
- [x] `src/bot/backtest/walk_forward.py` — Walk-forward IS/OOS analysis (5 windows)
- [x] `src/bot/backtest/monte_carlo.py` — Trade-order randomization (1000 iterations)
- [x] `src/bot/backtest/report.py` — Self-contained HTML report + Chart.js
- [x] `src/bot/backtest/models.py` — Pydantic types: BacktestConfig, SimulatedTrade, etc.
- [x] API: `POST /api/v2/backtest` + `POST /api/v2/backtest/report`

### Test: 140 nuovi

---

## Phase 6: VPS Deployment — DONE (2026-03-28)

### Componenti implementati
- [x] `docker/bot.Dockerfile` — Multi-stage Python 3.11-slim, healthcheck
- [x] `docker/dashboard.Dockerfile` — Node 20 build → nginx:alpine serve
- [x] `docker/nginx.conf` — Reverse proxy, rate limiting, WebSocket, security headers, SSL-ready
- [x] `docker/postgres/init.sql` — Full PostgreSQL schema (6 tables + indexes)
- [x] `docker/postgres/seed.sql` — Initial bankroll seeding
- [x] `docker-compose.yml` — 5 services: bot, dashboard, postgres, nginx, prometheus
- [x] `src/bot/storage/postgres.py` — asyncpg adapter (mirrors Database API)
- [x] `src/bot/storage/factory.py` — DB factory (SQLite vs PostgreSQL)
- [x] `scripts/healthcheck.sh` + `scripts/backup.sh`

---

## Phase 7: Monitoring & Observability — DONE (2026-03-28)

### Structured JSON Logging
- [x] `src/bot/monitoring/logging_config.py` — BotJsonFormatter (python-json-logger)
- [x] Auto-detect: JSON in Docker (`LOG_FORMAT=json` o `/.dockerenv`), pretty in terminal
- [x] Librerie noisy silenziate (httpx, ccxt, websockets, uvicorn.access)

### Prometheus Metrics (20+ metriche)
- [x] `src/bot/monitoring/metrics.py` — Tutti i contatori/gauge/histogram:
  - Trade: `trades_total`, `trades_resolved_total`, `pnl_total_usd`, `pnl_per_trade_usd`, `bet_size_usd`, `open_trades`
  - Signal: `signals_evaluated_total`, `signal_confidence`
  - Exchange: `exchange_latency_ms`, `exchange_errors_total`, `exchange_up`, `exchange_price_usd`
  - Risk: `risk_events_total`, `drawdown_pct`, `circuit_breaker_active`
  - Execution: `execution_checks_failed_total`, `execution_latency_ms`
  - WebSocket: `ws_clients_connected`, `ws_messages_sent_total`, `ws_messages_dropped_total`
  - Database: `db_query_latency_ms`, `db_errors_total`
  - System: `resolver_cycles_total`, `uptime_seconds`
- [x] `GET /metrics` — Prometheus scrape endpoint (no auth)

### Alert Rules (12 regole)
- [x] `docker/prometheus/alerts.yml`:
  - NoTradesExecuted, HighLossRate
  - ExchangeDown, PrimaryExchangeDown, HighExchangeLatency, ExchangeErrors
  - CircuitBreakerTripped, HighDrawdown, RiskEventsSpike
  - WSMessagesDrop, SlowDBQueries, DBErrors, BotDown

### Docker Integration
- [x] Prometheus container in docker-compose (scrape ogni 15s)
- [x] `docker/prometheus/prometheus.yml` — scrape config
- [x] `LOG_FORMAT=json` nel bot container

### Componenti instrumentati
- executor.py (execution latency, trade counter, checks failed)
- resolver.py (resolved counter, PnL histogram, resolver cycles)
- exchange_manager.py (exchange up/latency/price)
- ws_broker.py (client count, messages sent/dropped)
- main.py (signal evaluation metrics)

---

## Phase 8: Security Hardening — DONE (2026-03-28)

### Implementato
- [x] **CORS middleware** — configurable origins via SecurityConfig
- [x] **App-level rate limiting** — token bucket per-IP (120 rpm default, 10s burst)
- [x] **Request body size limit** — 1MB default (nginx + app level)
- [x] **SecurityMiddleware** — rate limit + body size + headers + audit in un modulo
- [x] **Security headers** — CSP, Permissions-Policy (nginx + FastAPI backup)
- [x] **Audit logging** — structured log di tutte le chiamate /api/v2/* e 4xx/5xx
- [x] **Dual API key rotation** — primary + secondary per zero-downtime
- [x] **SecurityConfig** — cors_origins, max_body_size, rate_limit_rpm, api_key_secondary
- [x] **nginx hardened** — CSP, Permissions-Policy, client_max_body_size 1m
- [x] Input validation via Pydantic (pre-existing)
- [x] SQL injection prevention via parameterized queries (pre-existing)

---

## Phase 9: Strategy Evaluation — DONE (2026-03-28)

### Componenti implementati
- [x] `src/bot/backtest/evaluator.py` — StrategyEvaluator (orchestrator multi-strategy, 5 bots)
- [x] `src/bot/backtest/comparison.py` — Statistical comparison:
  - Composite scoring (Sharpe 30%, PF 20%, WR 15%, MC 15%, WF 10%, RF 10%)
  - Wilson score 95% CI for win rate
  - Mean CI for P&L per trade
  - Chi-square test on win rate homogeneity
  - CSV export
- [x] `src/bot/backtest/comparison_report.py` — HTML report:
  - Ranking table with composite scores
  - Sharpe ratio bar chart (Chart.js)
  - Equity curves overlay
  - Chi-square significance results
  - Per-strategy detail cards
- [x] API endpoints:
  - `POST /api/v2/backtest/evaluate` — JSON
  - `POST /api/v2/backtest/evaluate/report` — HTML
  - `POST /api/v2/backtest/evaluate/csv` — CSV download
- [x] `tests/test_backtest/test_evaluation.py` — 29 test

### Test: 29 nuovi (438 totali)

---

## Phase 10: Optimization & Polish — DONE (2026-03-31)

### Refactoring
- [x] **ACTIVE_BOTS centralizzato** — `core/types.py` single source of truth (was duplicated in 5 files: main.py, resolver.py, app.py, server.py, evaluator.py)
- [x] **Executor DB abstraction** — `Database.deduct_fee()` method (was raw SQL in executor.py)
- [x] **PostgreSQL parity** — `deduct_fee()` also added to `postgres.py`

### OpenAPI/Swagger
- [x] `/docs` — Swagger UI enabled (was `docs_url=None`)
- [x] `/redoc` — ReDoc enabled
- [x] Router tags: `Dashboard`, `Backtest & Evaluation`
- [x] App metadata: title, description, version

### Integration Tests E2E (`tests/test_integration.py`)
- [x] Full pipeline: Strategy → Executor → DB (MOMENTUM, TURBO_CVD, TURBO_VWAP)
- [x] Execute → Resolve → P&L (WIN/LOSS bankroll updates)
- [x] Multiple sequential trades with cumulative P&L
- [x] Risk integration (VPN block, low bankroll block)
- [x] EventBus trade.placed event verification
- [x] Gas fee deduction via DB method
- [x] ACTIVE_BOTS central constant validation

### Stress Tests (`tests/test_stress.py`)
- [x] 50 concurrent BUY_YES through executor
- [x] 100 concurrent mixed signals (4 strategy/asset/signal combos)
- [x] WindowTracker concurrency (100 concurrent records)
- [x] Concurrent bankroll reads/writes
- [x] Bulk resolver (50 trades insert + resolve)
- [x] PnL calculation batch consistency

### Load Tests (`tests/test_load_dashboard.py`)
- [x] 50 concurrent GET per endpoint (overview, bots, signals, trades)
- [x] 100 concurrent mixed endpoint calls
- [x] Rate limiter validation (429 responses under load)
- [x] Authenticated endpoints under load
- [x] Response time assertions (< 500ms API, < 200ms HTML)
- [x] WSBroker: 1000 messages + 100 concurrent publishes
- [x] EventBus: 1000 events throughput

### Test: 40 nuovi (478 totali)

---

## Pre-Go-Live Checklist

### Tecnico
- [ ] Tutti i test passano
- [ ] Zero console errors/warnings
- [ ] Monitoring setup completo
- [ ] Backup testato e funzionante
- [ ] Rollback plan documentato
- [ ] Disaster recovery testato

### Finanziario
- [ ] Paper trading validato (>55% win rate minimo)
- [ ] Backtest results reviewed
- [ ] Risk parameters conservativi
- [ ] Position size iniziale: micro ($1-5 per trade)
- [ ] Risk management policy scritta

---

## Architettura Corrente (snapshot 2026-03-28)

```
src/bot/
├── main.py                       # Orchestrator + metrics publisher
├── config.py                     # Pydantic Settings (ExchangeConfig, dex_enabled)
├── core/
│   ├── events.py                 # EventBus pub/sub
│   └── types.py                  # Enum + dataclass
├── strategies/                   # 5 strategy implementations
├── execution/
│   ├── executor.py               # 12 pre-check + order + Prometheus metrics
│   ├── sizer.py                  # 1/3 fractional Kelly
│   ├── risk.py                   # Drawdown, correlation, circuit breaker
│   └── resolver.py               # Gamma API resolution + metrics publishing
├── feeds/
│   ├── binance_ws.py             # Multi-asset WS + REST (PRIMARY)
│   ├── rsi_feed.py               # RSI-14 + BB calculator
│   ├── exchange_adapter.py       # ABC + NormalizedTick + ExchangeHealth
│   ├── exchange_manager.py       # Multi-exchange orchestrator + Prometheus
│   └── adapters/
│       ├── binance.py            # Primary adapter (full indicators)
│       ├── ccxt_adapter.py       # CEX adapter (WS watch_ticker + REST fallback)
│       └── dexscreener.py        # DEX aggregator (80+ DEXes)
├── market/
│   ├── finder.py                 # Polymarket discovery
│   └── orderbook.py              # CLOB 5s cache
├── storage/
│   ├── database.py               # Async SQLite
│   ├── postgres.py               # Async PostgreSQL (asyncpg)
│   └── factory.py                # DB adapter factory
├── backtest/                     # Engine, walk-forward, Monte Carlo, reports
├── dashboard/
│   ├── app.py                    # FastAPI + HTML + /metrics endpoint
│   ├── server.py                 # REST API v2 + /exchanges health
│   ├── backtest_api.py           # Backtest API endpoints
│   ├── auth.py                   # API key auth
│   ├── ws_broker.py              # In-memory pub/sub + Prometheus
│   ├── ws_stream.py              # WS + SSE (API key auth)
│   └── ws_bridge.py              # EventBus → WSBroker bridge
├── monitoring/
│   ├── metrics.py                # 20+ Prometheus counters/gauges/histograms
│   └── logging_config.py         # Structured JSON logging
└── network/
    └── vpn_guard.py              # VPN tunnel check

docker/
├── bot.Dockerfile
├── dashboard.Dockerfile
├── nginx.conf
├── dashboard-nginx.conf
├── postgres/init.sql, seed.sql
└── prometheus/prometheus.yml, alerts.yml

docker-compose.yml                # bot + dashboard + postgres + nginx + prometheus
```

---

## Changelog

| Data | Fase | Azione |
|------|------|--------|
| 2026-03-26 | 0 | /init completato, CLAUDE.md creato, 9 agenti, skills installate |
| 2026-03-26 | F | 180 test scritti e verificati (13 file, 0.57s) |
| 2026-03-26 | 2 | Phase 2 completata — ExchangeAdapter ABC, BinanceAdapter, CCXTAdapter, ExchangeManager, 58 nuovi test (238 totali) |
| 2026-03-26 | 3 | Phase 3 completata — WSBroker, WS/SSE endpoints, WSBridge, 31 nuovi test (269 totali) |
| 2026-03-26 | 4 | Phase 4 completata — Vite + React 19 + TS + TailwindCSS v4 + Recharts, 14 file, build OK |
| 2026-03-28 | 5 | Phase 5 completata — Backtest engine, walk-forward, Monte Carlo, HTML reports, 140 nuovi test |
| 2026-03-28 | 6 | Phase 6 completata — Docker Compose (bot+dashboard+postgres+nginx), asyncpg adapter, healthcheck, backup |
| 2026-03-28 | 2 | Phase 2 completata — DexScreenerAdapter (DEX), CCXTAdapter WS mode, 4 CEX default attivi, /api/v2/exchanges |
| 2026-03-28 | 3 | Phase 3 completata — API key auth WS/SSE, metrics.updated publishing, periodic metrics 30s |
| 2026-03-28 | 7 | Phase 7 completata — Prometheus metrics (20+), JSON logging, 12 alert rules, Prometheus container |
| 2026-03-28 | 8 | Phase 8 completata — CORS, rate limiting, CSP, audit logging, dual API key rotation, SecurityMiddleware |
| 2026-03-28 | 9 | Phase 9 completata — StrategyEvaluator, comparison (chi-square, Wilson CI), HTML report, API+CSV, 29 test |
| 2026-03-31 | 10 | Phase 10 completata — ACTIVE_BOTS refactor, OpenAPI docs, E2E/stress/load tests, 40 nuovi test (478 totali) |
