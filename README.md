# btc-bot-v2 — Polymarket Up/Down Trading Bot

> Multi-strategy automated trading bot for Polymarket binary markets. Watches Binance Futures feeds (BTC/ETH/SOL), generates directional signals using 5 profitable strategies, and places paper trades with full risk management.

**Status:** Paper Trading (MODE=paper)
**Version:** 2.0.0
**Test Coverage:** 478 passing tests (18.55s)

---

## 🎯 Overview

This bot implements a production-ready trading system that:
- **Monitors real-time feeds** from Binance (WebSocket + REST fallback)
- **Generates signals** using 5 backtested, profitable strategies
- **Manages risk** with position sizing, drawdown limits, correlation checks, circuit breakers
- **Places paper trades** on Polymarket Up/Down binary markets
- **Resolves outcomes** via Gamma API (30s polling)
- **Provides observability** with FastAPI dashboard, Prometheus metrics, structured JSON logging

Only strategies with **positive P&L in v1 data** are enabled:
- TurboCVD (ETH, 88% win rate, +$47.92)
- TurboVWAP (ETH, 83.8% win rate, +$19.78)
- Momentum (BTC, 96.4% win rate, +$12.80)
- Momentum (SOL, 100% win rate, +$6.28)
- Bollinger (BTC, 100% win rate, +$0.49)

---

## 📋 Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| **Language** | Python 3.11+ | Fully async (asyncio) |
| **Config** | Pydantic v2 + pydantic-settings | Type-safe, env-based |
| **HTTP/WebSocket** | httpx + websockets | Async client, real-time feeds |
| **Database** | aiosqlite (local) / asyncpg (Docker) | ACID, snapshots, rolling stats |
| **API/Dashboard** | FastAPI + Uvicorn | Port 5003, dark theme, real-time WebSocket |
| **Monitoring** | Prometheus + python-json-logger | 20+ metrics, structured JSON logs |
| **Testing** | pytest + pytest-asyncio | 478 tests, >70% critical path coverage |
| **Deployment** | Docker Compose | bot + dashboard + PostgreSQL + Nginx + Prometheus |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    MAIN ORCHESTRATOR                             │
│                    (src/bot/main.py)                             │
│  Async event loop: BinanceFeed → Strategies → Executor → Resolver│
└────────────────────────────────────────────────────────────────┘

         ┌────────────────────┬─────────────────────────┐
         │                    │                         │
         ▼                    ▼                         ▼
    ┌─────────────┐      ┌──────────────┐      ┌─────────────────┐
    │  Feeds      │      │  Strategies  │      │  Execution      │
    ├─────────────┤      ├──────────────┤      ├─────────────────┤
    │ • Binance   │      │ • TurboCVD   │      │ • Kelly sizer   │
    │   (WS+REST) │      │ • TurboVWAP  │      │ • Risk mgmt     │
    │ • CCXT CEX  │      │ • Momentum   │      │ • Resolver      │
    │ • DexScreen │      │ • Bollinger  │      │ • Market finder │
    │ • ExchgMgr  │      │ • RSIFeed    │      └─────────────────┘
    └─────────────┘      └──────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                    STORAGE & MONITORING                          │
├──────────────────────────────────────────────────────────────────┤
│ • Database (CRUD, atomicity, stats)                              │
│ • Prometheus metrics (20+ metrics, 12 alert rules)               │
│ • JSON structured logging                                         │
│ • FastAPI Dashboard (positions, PnL, health, auth)               │
└──────────────────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
btc-bot-v2/
├── src/bot/
│   ├── main.py                  # Entry point — async event loop orchestrator
│   ├── config.py                # Pydantic v2 config (typed, env-based)
│   ├── __main__.py              # python -m bot entry
│   │
│   ├── core/
│   │   ├── types.py             # ACTIVE_BOTS, SignalResult, TradeResult (SSoT)
│   │   └── events.py            # EventBus pub/sub for decoupling
│   │
│   ├── feeds/                   # Data ingestion
│   │   ├── binance_ws.py        # Binance Futures WebSocket (BTC/ETH/SOL)
│   │   ├── rsi_feed.py          # RSI-14, Bollinger Bands, 1min candles
│   │   ├── exchange_manager.py  # Multi-exchange consensus (median + MAD)
│   │   ├── exchange_adapter.py  # Adapter interface
│   │   └── adapters/
│   │       ├── binance.py       # Binance REST/WS adapter
│   │       ├── ccxt_adapter.py  # CEX (coinbase, kraken, bybit, okx)
│   │       └── dexscreener.py   # DEX aggregator (80+ DEX)
│   │
│   ├── strategies/              # Trading signals
│   │   ├── base.py              # BaseStrategy interface
│   │   ├── momentum.py          # Momentum (BTC, SOL)
│   │   ├── turbo_cvd.py         # CVD-based (ETH)
│   │   ├── turbo_vwap.py        # VWAP deviation (ETH)
│   │   └── bollinger.py         # Bollinger Bands (BTC)
│   │
│   ├── execution/               # Trading engine
│   │   ├── executor.py          # 12-stage trade execution pipeline
│   │   ├── sizer.py             # Kelly criterion position sizing
│   │   ├── resolver.py          # Outcome resolution (Gamma API, 30s poll)
│   │   └── risk_manager.py      # Drawdown, correlation, circuit breaker
│   │
│   ├── storage/                 # Data persistence
│   │   ├── database.py          # CRUD, atomicity, stats, rolling aggregates
│   │   ├── factory.py           # aiosqlite vs asyncpg selector
│   │   └── postgres.py          # PostgreSQL schema + migrations
│   │
│   ├── backtest/                # Backtesting & analysis
│   │   ├── engine.py            # Replay engine with slippage
│   │   ├── walk_forward.py      # Walk-forward validation
│   │   ├── monte_carlo.py       # Bootstrap resampling
│   │   ├── evaluator.py         # Sharpe, Sortino, win rate, etc.
│   │   ├── comparison.py        # Chi-square test, Wilson CI
│   │   ├── report.py            # HTML reports
│   │   ├── data_provider.py     # Historical data
│   │   └── comparison_report.py # Strategy comparison
│   │
│   ├── dashboard/               # Web API & UI
│   │   ├── server.py            # FastAPI app, OpenAPI docs
│   │   ├── auth.py              # X-API-Key auth, audit logging
│   │   └── ws_bridge.py         # WebSocket pub/sub
│   │
│   ├── monitoring/              # Observability
│   │   ├── metrics.py           # Prometheus metrics (20+)
│   │   ├── logging.py           # JSON structured logging
│   │   └── alerts.py            # Alert rules (12)
│   │
│   ├── market/                  # Market interactions
│   │   └── polymarket.py        # Polymarket CLOB client
│   │
│   ├── network/                 # Network security
│   │   └── vpn_guard.py         # VPN check before trading
│   │
│   └── risk/                    # Risk parameters
│       └── params.py            # Drawdown, circuit breaker configs
│
├── tests/
│   ├── conftest.py              # Pytest fixtures
│   ├── test_strategies/         # Strategy unit tests (38 tests)
│   ├── test_feeds/              # Feed & exchange tests (55 tests)
│   ├── test_storage/            # Database tests (22 tests)
│   ├── test_execution/          # Executor, sizer, resolver, risk (53 tests)
│   ├── test_dashboard/          # API & WebSocket tests (20 tests)
│   ├── test_backtest/           # Backtest suite tests (140 tests)
│   └── test_stress.py           # Load & stress tests (150 tests)
│
├── dashboard/                   # React frontend (Vite + React 19 + TS)
│   ├── src/
│   │   ├── components/          # Dashboard UI components
│   │   ├── hooks/               # React custom hooks (WebSocket, auth)
│   │   └── types/               # TypeScript types
│   ├── public/
│   └── dist/                    # Built static assets
│
├── docker/
│   ├── postgres/                # PostgreSQL schema & init scripts
│   ├── prometheus/              # Prometheus config & alert rules
│   └── ssl/                     # TLS certificates
│
├── scripts/
│   ├── backtest.py              # Offline backtesting runner
│   └── health_check.py          # Health check utility
│
├── docker-compose.yml           # Full stack: bot + dashboard + DB + Prometheus + Nginx
├── ROADMAP.md                   # Development roadmap (10 phases completed)
├── CLAUDE.md                    # Architecture & single source of truth
├── pyproject.toml               # Dependencies & pytest config
└── .env.example                 # Environment template
```

---

## 🚀 Quick Start

### 1. Prerequisites

- **Python 3.11+**
- **pip** or **uv**
- **Docker** (optional, for full stack)

### 2. Install

```bash
cd btc-bot-v2

# Install dependencies
pip install -e .

# Or with uv:
uv sync
```

### 3. Configure

```bash
# Copy template
cp .env.example .env

# Edit .env with your settings:
MODE=paper                    # paper or live
POLYMARKET_API_KEY=...       # Polymarket credentials
BINANCE_API_KEY=...          # (optional, for REST fallback)
BINANCE_SECRET=...           # (optional)
DB_URL=sqlite:///btc_bot_v2.db  # Local SQLite
API_KEY=dev-key-123          # Dashboard auth
```

### 4. Run Bot (Paper Trading)

```bash
# Terminal 1: Start bot
python -m bot

# Terminal 2: Start dashboard (optional)
cd dashboard && npm run dev
```

The bot will:
1. Connect to Binance Futures WebSocket
2. Load strategies from `ACTIVE_BOTS` (src/bot/core/types.py)
3. Generate signals every 6-15 seconds (strategy-dependent)
4. Execute paper trades with full risk checks
5. Resolve outcomes via Gamma API (30s polling)
6. Log everything to stdout (JSON format in Docker)

### 5. Run Tests

```bash
# All tests (478 tests)
pytest -v

# Specific module
pytest tests/test_strategies/ -v

# With coverage
pytest --cov=src/bot --cov-report=html
```

### 6. Run Dashboard API

```bash
# Standalone (requires running bot)
python -c "from src.bot.dashboard.server import app; import uvicorn; uvicorn.run(app, host='0.0.0.0', port=5003)"

# Or via bot (if integrated)
python -m bot  # Includes dashboard on :5003
```

**Dashboard endpoints:**
- `GET /docs` — OpenAPI Swagger UI
- `GET /api/v2/health` — Bot health
- `GET /api/v2/positions` — Active positions (JSON)
- `GET /api/v2/pnl` — Cumulative P&L
- `GET /api/v2/params` — Active strategy params
- `GET /api/v2/logs?limit=100` — Recent trades
- `WebSocket /ws/stream` — Real-time metrics (auth required)

**Auth:** Add header `X-API-Key: <API_KEY>` to requests.

---

## 🐳 Docker Deployment

### Full Stack (Bot + Dashboard + PostgreSQL + Prometheus + Nginx)

```bash
docker-compose up -d

# Logs
docker-compose logs -f bot

# Health check
curl http://localhost/api/v2/health

# Prometheus metrics
curl http://localhost:9090
```

**Services:**
- **Bot:** `:5003` (dashboard API) + `:8000` (Prometheus metrics)
- **Dashboard:** `:3000` (React frontend)
- **PostgreSQL:** `:5432` (persistent storage)
- **Prometheus:** `:9090` (metrics + alerts)
- **Nginx:** `:80` / `:443` (reverse proxy + TLS)

See `docker-compose.yml` for full configuration.

---

## 📊 Strategies

All strategies are backtested with real v1 data. Only profitable ones are active:

### 1. **TurboCVD** (ETH, 6s interval)
- **Signal:** CVD (Cumulative Volume Delta) crosses threshold
- **Win Rate:** 88.0% (v1)
- **P&L:** +$47.92 (v1)
- **Code:** `src/bot/strategies/turbo_cvd.py`
- **Max orders/window:** 30

### 2. **TurboVWAP** (ETH, 6s interval)
- **Signal:** Price deviation from VWAP (Volume-Weighted Average Price)
- **Win Rate:** 83.8% (v1)
- **P&L:** +$19.78 (v1)
- **Code:** `src/bot/strategies/turbo_vwap.py`
- **Max orders/window:** 30

### 3. **Momentum** (BTC & SOL, 15s interval)
- **Signal:** RSI + momentum indicator
- **Win Rate:** 96.4% (BTC), 100% (SOL) (v1)
- **P&L:** +$12.80 (BTC), +$6.28 (SOL) (v1)
- **Code:** `src/bot/strategies/momentum.py`
- **Max orders/window:** 10

### 4. **Bollinger Bands** (BTC, 15s interval)
- **Signal:** Price outside Bollinger Bands (20, 2 std)
- **Win Rate:** 100% (v1)
- **P&L:** +$0.49 (v1)
- **Code:** `src/bot/strategies/bollinger.py`
- **Max orders/window:** 5

### Adding New Strategies

Create a new file in `src/bot/strategies/`:

```python
from src.bot.strategies.base import BaseStrategy
from src.bot.core.types import SignalResult, Signal

class MyStrategy(BaseStrategy):
    async def generate_signal(self, feed: FeedSnapshot) -> SignalResult:
        # Your logic here
        return SignalResult(
            signal=Signal.BUY_YES,  # or BUY_NO, SKIP
            confidence=0.75,
            reasoning="explanation"
        )
```

Then register in `src/bot/core/types.py`:
```python
ACTIVE_BOTS = [
    BotConfig(strategy="MyStrategy", asset="BTC"),
    ...
]
```

---

## 📈 Backtesting

### Run Backtest Suite

```bash
python scripts/backtest.py --start 2024-01-01 --end 2024-03-31 --strategies momentum,turbo_cvd
```

**Output:**
- `backtest_report.html` — Interactive results (equity curve, trades, stats)
- `comparison.csv` — Strategy metrics (Sharpe, win rate, max DD, etc.)
- Walk-forward validation (80/20 split)
- Monte Carlo resampling (1000 iterations)

**Metrics calculated:**
- Win Rate, Avg Win/Loss
- Sharpe Ratio, Sortino Ratio
- Max Drawdown, Recovery Factor
- Profit Factor, Calmar Ratio

---

## 🔒 Risk Management

### Features

1. **Kelly Criterion Sizing** — Position size based on win rate & risk/reward
2. **Drawdown Limit** — Circuit breaker at configured max drawdown
3. **Correlation Check** — Avoid correlated entries (default: max 0.7)
4. **Window Cap** — Max orders per strategy per time window
5. **VPN Guard** — Verify VPN before live trading
6. **Audit Logging** — All trades logged with timestamp, parameters, outcome

### Configuration

Edit `.env`:
```env
# Risk parameters
MAX_DRAWDOWN=0.15          # 15% max drawdown
MAX_CORRELATION=0.7        # Avoid correlated trades
KELLY_FRACTION=0.25        # Conservative Kelly (25%)
MIN_BANKROLL=$100          # Minimum balance to trade
WINDOW_MINUTES=5           # Order window for caps
```

---

## 📊 Monitoring & Observability

### Prometheus Metrics (20+)

```
# Counter
btc_bot_trades_placed_total{strategy="momentum",asset="BTC"}
btc_bot_trades_resolved_total{outcome="win"}

# Gauge
btc_bot_bankroll
btc_bot_max_drawdown
btc_bot_open_positions

# Histogram
btc_bot_trade_duration_seconds
btc_bot_pnl_per_trade

# Info
btc_bot_info{version="2.0.0",mode="paper"}
```

### JSON Logging

```json
{
  "timestamp": "2026-04-02T10:15:30.123Z",
  "level": "INFO",
  "message": "Trade executed",
  "trade_id": "uuid",
  "strategy": "momentum",
  "signal": "BUY_YES",
  "entry_price": 63500.25,
  "size": 0.01,
  "outcome": null
}
```

### Alert Rules (12)

- High error rate (>5% of trades failing)
- Feed disconnection (>5 sec lag)
- Bankroll depletion (<20% of initial)
- Extreme drawdown (>10%)
- Correlation spike (>0.85)
- Database lag (>500ms)

---

## 🧪 Testing

### Test Suite (478 tests, 18.55s)

```
tests/
├── test_strategies/       (38 tests)
│   ├── test_momentum.py      — BUY/NO/SKIP, confidence, entry validation
│   ├── test_turbo_cvd.py     — CVD threshold, signal generation
│   ├── test_turbo_vwap.py    — VWAP deviation detection
│   └── test_bollinger.py     — Bollinger Bands logic
│
├── test_feeds/            (55 tests)
│   ├── test_binance_ws.py    — WebSocket state, snapshots
│   ├── test_rsi_feed.py      — RSI-14, Bollinger candle boundary
│   ├── test_exchange_manager.py — Consensus, MAD outlier detection
│   ├── test_ccxt_adapter.py  — Multi-exchange fallback
│   └── test_binance_adapter.py  — REST/WS modes
│
├── test_storage/          (22 tests)
│   └── test_database.py      — CRUD, atomicity, rolling stats
│
├── test_execution/        (53 tests)
│   ├── test_executor.py      — 12-stage pipeline, pre-checks
│   ├── test_sizer.py         — Kelly criterion, warmup
│   ├── test_resolver.py      — PnL calculation, outcome inference
│   └── test_risk.py          — Drawdown, correlation, circuit breaker
│
├── test_dashboard/        (20 tests)
│   ├── test_api.py           — Health, positions, PnL endpoints
│   ├── test_ws_bridge.py     — WebSocket auth, pub/sub
│   └── test_ws_broker.py     — Message routing
│
├── test_backtest/         (140 tests)
│   ├── test_backtest_suite.py  — Engine, walk-forward, Monte Carlo
│   └── test_evaluation.py      — Metrics, comparison, reports
│
└── test_stress.py         (150 tests)
    └── Load testing, 10K trades/sec, memory profiling
```

### Run Tests

```bash
# All tests
pytest -v

# Specific file
pytest tests/test_strategies/test_momentum.py -v

# Watch mode
pytest-watch

# Coverage report
pytest --cov=src/bot --cov-report=html
open htmlcov/index.html
```

---

## 🔐 Security

### Implemented

1. **API Key Rotation** — Automatic rotation every 24h (dashboard auth)
2. **CORS Policy** — Whitelist allowed origins
3. **Rate Limiting** — 100 req/min per IP
4. **CSP Headers** — Content Security Policy
5. **Audit Logging** — All trades logged with user, timestamp, outcome
6. **VPN Check** — Verify VPN before live trading
7. **Environment Variables** — No hardcoded secrets

### Checklist Before Going Live

- [ ] Review all risk parameters in `.env`
- [ ] Backtest all strategies with 2024+ data
- [ ] Test paper trading for 48h+ with >55% win rate
- [ ] Set up monitoring (Prometheus, alerts)
- [ ] Enable VPN guard
- [ ] Rotate API keys
- [ ] Test recovery after crashes (db checkpoint, position recovery)
- [ ] Review Polymarket contract specs & fee structure

---

## 🤝 Contributing

### Code Style

- **Black** for formatting
- **Type hints** on all functions
- **Async/await** for I/O-bound operations
- **Pydantic** for all data validation

### Adding Features

1. Create a new branch: `git checkout -b feature/my-feature`
2. Write tests first (TDD)
3. Implement feature
4. Ensure all tests pass: `pytest -v`
5. Submit PR

### Debugging

```bash
# Enable debug logging
LOGLEVEL=DEBUG python -m bot

# Profiling
python -m cProfile -s cumtime -m bot > profile.txt

# Memory usage
python -m memory_profiler bot.py

# Database inspection
sqlite3 btc_bot_v2.db "SELECT * FROM trades LIMIT 10;"
```

---

## 📝 License

Proprietary — All rights reserved.

---

## 🔗 References

- **Polymarket:** https://polymarket.com/
- **Binance Futures:** https://www.binance.com/en/futures
- **FastAPI:** https://fastapi.tiangolo.com/
- **Prometheus:** https://prometheus.io/

---

## 📞 Support

For issues, see `CLAUDE.md` for architecture details or `ROADMAP.md` for development history.

Last updated: 2026-04-02
