"""Prometheus metrics for btc-bot-v2.

All metrics are defined here as module-level singletons.
Import and use them from any module:

    from bot.monitoring.metrics import TRADES_TOTAL, PNL_GAUGE
    TRADES_TOTAL.labels(strategy="MOMENTUM", asset="BTC", outcome="WIN").inc()
    PNL_GAUGE.labels(strategy="MOMENTUM", asset="BTC").set(12.50)
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Info

# ── Bot info ────────────────────────────────────────────

BOT_INFO = Info("bot", "Bot metadata")
BOT_INFO.info({
    "version": "2.0.0",
    "mode": "paper",
    "name": "btc-bot-v2",
})

# ── Trade metrics ───────────────────────────────────────

TRADES_TOTAL = Counter(
    "trades_total",
    "Total trades executed",
    ["strategy", "asset", "signal"],
)

TRADES_RESOLVED = Counter(
    "trades_resolved_total",
    "Total trades resolved",
    ["strategy", "asset", "outcome"],
)

PNL_TOTAL = Gauge(
    "pnl_total_usd",
    "Cumulative realized P&L in USD",
    ["strategy", "asset"],
)

PNL_PER_TRADE = Histogram(
    "pnl_per_trade_usd",
    "P&L distribution per trade in USD",
    ["strategy", "asset"],
    buckets=[-5, -2, -1, -0.5, 0, 0.5, 1, 2, 5, 10],
)

BANKROLL_GAUGE = Gauge(
    "bankroll_usd",
    "Current bankroll in USD",
    ["strategy", "asset"],
)

WIN_RATE_GAUGE = Gauge(
    "win_rate_pct",
    "Win rate percentage",
    ["strategy", "asset"],
)

BET_SIZE = Histogram(
    "bet_size_usd",
    "Bet size distribution in USD",
    ["strategy", "asset"],
    buckets=[0.1, 0.5, 1, 2, 3, 5, 10],
)

OPEN_TRADES = Gauge(
    "open_trades",
    "Number of currently open trades",
    ["strategy", "asset"],
)

# ── Signal metrics ──────────────────────────────────────

SIGNALS_EVALUATED = Counter(
    "signals_evaluated_total",
    "Total signal evaluations",
    ["strategy", "asset", "signal"],
)

SIGNAL_CONFIDENCE = Histogram(
    "signal_confidence",
    "Signal confidence distribution",
    ["strategy", "asset"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# ── Exchange metrics ────────────────────────────────────

EXCHANGE_LATENCY = Histogram(
    "exchange_latency_ms",
    "Exchange API latency in milliseconds",
    ["exchange"],
    buckets=[10, 25, 50, 100, 250, 500, 1000, 2500, 5000],
)

EXCHANGE_ERRORS = Counter(
    "exchange_errors_total",
    "Exchange API errors",
    ["exchange", "error_type"],
)

EXCHANGE_UP = Gauge(
    "exchange_up",
    "Exchange connection status (1=connected, 0=down)",
    ["exchange"],
)

EXCHANGE_PRICE = Gauge(
    "exchange_price_usd",
    "Latest price per exchange per asset",
    ["exchange", "asset"],
)

# ── Risk metrics ────────────────────────────────────────

RISK_EVENTS = Counter(
    "risk_events_total",
    "Risk events triggered",
    ["event_type", "strategy", "asset"],
)

DRAWDOWN_GAUGE = Gauge(
    "drawdown_pct",
    "Current drawdown percentage",
    ["strategy", "asset"],
)

CIRCUIT_BREAKER_ACTIVE = Gauge(
    "circuit_breaker_active",
    "Circuit breaker status (1=tripped, 0=normal)",
    ["strategy"],
)

# ── Execution metrics ───────────────────────────────────

EXECUTION_CHECKS_FAILED = Counter(
    "execution_checks_failed_total",
    "Pre-trade checks that blocked execution",
    ["check_name", "strategy", "asset"],
)

EXECUTION_LATENCY = Histogram(
    "execution_latency_ms",
    "Trade execution latency in milliseconds",
    ["strategy"],
    buckets=[10, 50, 100, 250, 500, 1000, 2000],
)

# ── WebSocket metrics ───────────────────────────────────

WS_CLIENTS_CONNECTED = Gauge(
    "ws_clients_connected",
    "Number of WebSocket clients currently connected",
)

WS_MESSAGES_SENT = Counter(
    "ws_messages_sent_total",
    "WebSocket messages sent to clients",
    ["channel"],
)

WS_MESSAGES_DROPPED = Counter(
    "ws_messages_dropped_total",
    "WebSocket messages dropped (queue overflow)",
    ["channel"],
)

# ── Database metrics ────────────────────────────────────

DB_QUERY_LATENCY = Histogram(
    "db_query_latency_ms",
    "Database query latency in milliseconds",
    ["operation"],
    buckets=[1, 5, 10, 25, 50, 100, 250, 500],
)

DB_ERRORS = Counter(
    "db_errors_total",
    "Database errors",
    ["operation"],
)

# ── System metrics ──────────────────────────────────────

RESOLVER_CYCLES = Counter(
    "resolver_cycles_total",
    "Resolver polling cycles executed",
)

FEED_WARMUP_SECONDS = Gauge(
    "feed_warmup_seconds",
    "Feed warmup duration in seconds",
)

UPTIME_SECONDS = Gauge(
    "uptime_seconds",
    "Bot uptime in seconds",
)
