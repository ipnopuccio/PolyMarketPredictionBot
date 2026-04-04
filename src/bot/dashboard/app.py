"""FastAPI dashboard for Polymarket Bot v2 — single-page dark-theme UI."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from bot.storage.database import Database
from bot.config import settings
from bot.core.types import ACTIVE_BOTS
from bot.dashboard.log_buffer import LogBuffer
from bot.dashboard.server import router as api_v2_router
from bot.dashboard.backtest_api import router as backtest_router
from bot.dashboard.security import SecurityMiddleware
from bot.dashboard.ws_broker import WSBroker
from bot.dashboard.ws_stream import create_ws_router


def create_app(
    db: Database,
    broker: WSBroker | None = None,
    exchange_mgr: Any = None,
    selector: Any = None,
) -> FastAPI:
    app = FastAPI(
        title="Polymarket Bot v2",
        description="Multi-strategy Polymarket Up/Down trading bot API.",
        version="2.0.0",
    )

    # Shared state for the API router
    app.state.db = db
    app.state.broker = broker
    app.state.exchange_mgr = exchange_mgr
    app.state.selector = selector

    # ── Security middleware (rate limit, body size, headers, audit) ──
    app.add_middleware(SecurityMiddleware)

    # ── CORS ────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["X-API-Key", "Content-Type"],
    )

    # Install log buffer (singleton) and mount authenticated API
    LogBuffer.install()
    app.include_router(api_v2_router)
    app.include_router(backtest_router)

    # Prometheus metrics endpoint (no auth — Prometheus needs to scrape it)
    @app.get("/metrics")
    async def prometheus_metrics():
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    # Mount WebSocket/SSE streaming (if broker provided)
    if broker is not None:
        ws_router = create_ws_router(broker)
        app.include_router(ws_router)

    # ── API endpoints ───────────────────────────────────────

    @app.get("/api/overview")
    async def api_overview():
        total_pnl = 0.0
        total_bankroll = 0.0
        total_trades = 0
        total_wins = 0
        total_resolved = 0

        for strat, asset in ACTIVE_BOTS:
            stats = await db.get_stats(strat, asset)
            bankroll = await db.get_bankroll(strat, asset)
            total_pnl += stats.get("total_pnl", 0.0)
            total_bankroll += bankroll
            total_trades += stats.get("trades", 0) + stats.get("open", 0)
            total_wins += stats.get("wins", 0)
            total_resolved += stats.get("trades", 0)

        win_rate = round((total_wins / total_resolved * 100), 1) if total_resolved else 0.0
        return {
            "total_pnl": round(total_pnl, 2),
            "total_bankroll": round(total_bankroll, 2),
            "total_trades": total_trades,
            "win_rate": win_rate,
            "mode": settings.mode,
        }

    @app.get("/api/bots")
    async def api_bots():
        results = []
        for strat, asset in ACTIVE_BOTS:
            stats = await db.get_stats(strat, asset)
            bankroll = await db.get_bankroll(strat, asset)
            # Try to get current signal state
            signal_states = await db.get_signal_states()
            sig = next(
                (s for s in signal_states if s["strategy"] == strat and s["asset"] == asset),
                None,
            )
            results.append({
                "strategy": strat,
                "asset": asset,
                "signal": sig["signal"] if sig else None,
                "confidence": round(sig["confidence"], 3) if sig and sig["confidence"] else 0.0,
                "win_rate": stats.get("win_rate", 0.0),
                "total_pnl": stats.get("total_pnl", 0.0),
                "bankroll": round(bankroll, 2),
                "trades": stats.get("trades", 0) + stats.get("open", 0),
                "open": stats.get("open", 0),
            })
        return results

    @app.get("/api/signals")
    async def api_signals():
        all_states = await db.get_signal_states()
        bot_keys = {(s, a) for s, a in ACTIVE_BOTS}
        return [
            s for s in all_states if (s["strategy"], s["asset"]) in bot_keys
        ]

    @app.get("/api/trades")
    async def api_trades(limit: int = Query(default=50, le=200)):
        all_trades = await db.get_recent_trades(limit=limit * 3)
        bot_keys = {(s, a) for s, a in ACTIVE_BOTS}
        filtered = [t for t in all_trades if (t["strategy"], t["asset"]) in bot_keys]
        return filtered[:limit]

    @app.get("/api/risk-events")
    async def api_risk_events():
        return await db.get_recent_risk_events(limit=30)

    # ── HTML dashboard ──────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return DASHBOARD_HTML

    return app


# ════════════════════════════════════════════════════════════
# Embedded Jinja2-style HTML template (plain HTML + JS fetch)
# ════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket Bot v2</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0d1117;
    --card: #161b22;
    --border: #30363d;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --green: #3fb950;
    --red: #f85149;
    --blue: #58a6ff;
    --gold: #d29922;
    --purple: #bc8cff;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    line-height: 1.5;
    padding: 0;
  }

  .mono { font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', 'Consolas', monospace; }

  /* ── Top Bar ───────────────────────── */
  .topbar {
    display: flex;
    align-items: center;
    gap: 32px;
    padding: 16px 28px;
    background: var(--card);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }
  .topbar-title {
    font-size: 18px;
    font-weight: 700;
    color: var(--blue);
    white-space: nowrap;
  }
  .topbar-stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .topbar-stat .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-dim);
  }
  .topbar-stat .value {
    font-size: 22px;
    font-weight: 700;
  }
  .mode-badge {
    padding: 4px 14px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
  }
  .mode-paper { background: rgba(210,153,34,0.15); color: var(--gold); border: 1px solid var(--gold); }
  .mode-live  { background: rgba(63,185,80,0.15); color: var(--green); border: 1px solid var(--green); }

  .spacer { flex: 1; }

  /* ── Container ─────────────────────── */
  .container { max-width: 1400px; margin: 0 auto; padding: 24px; }

  .section-title {
    font-size: 14px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text-dim);
    margin-bottom: 14px;
    margin-top: 32px;
  }

  /* ── Bot Cards ─────────────────────── */
  .bot-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
    gap: 16px;
  }
  .bot-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    transition: border-color 0.2s;
  }
  .bot-card:hover { border-color: var(--blue); }

  .bot-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .bot-badge {
    font-size: 12px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 6px;
    background: rgba(88,166,255,0.12);
    color: var(--blue);
    white-space: nowrap;
  }
  .signal-arrow {
    font-size: 22px;
    font-weight: 900;
    line-height: 1;
  }
  .signal-up   { color: var(--green); }
  .signal-down { color: var(--red); }
  .signal-skip { color: var(--text-dim); }

  .confidence-bar-wrap {
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
  }
  .confidence-bar {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
  }

  .bot-stats {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 16px;
  }
  .bot-stat-item {
    display: flex;
    justify-content: space-between;
    font-size: 13px;
  }
  .bot-stat-item .lbl { color: var(--text-dim); }
  .bot-stat-item .val { font-weight: 600; }

  /* ── Tables ────────────────────────── */
  .tbl-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--card);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    text-align: left;
    padding: 10px 14px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-dim);
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    position: sticky;
    top: 0;
    background: var(--card);
  }
  td {
    padding: 8px 14px;
    border-bottom: 1px solid rgba(48,54,61,0.5);
    white-space: nowrap;
  }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(88,166,255,0.04); }

  .pnl-pos { color: var(--green); }
  .pnl-neg { color: var(--red); }
  .outcome-win  { color: var(--green); font-weight: 700; }
  .outcome-loss { color: var(--red); font-weight: 700; }
  .outcome-open { color: var(--gold); font-weight: 600; }

  /* ── Risk Events ───────────────────── */
  .risk-log {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    max-height: 280px;
    overflow-y: auto;
    padding: 4px 0;
  }
  .risk-row {
    padding: 6px 16px;
    font-size: 12px;
    display: flex;
    gap: 12px;
    border-bottom: 1px solid rgba(48,54,61,0.3);
  }
  .risk-row:last-child { border-bottom: none; }
  .risk-time { color: var(--text-dim); min-width: 150px; }
  .risk-type {
    font-weight: 600;
    min-width: 140px;
    color: var(--gold);
  }
  .risk-detail { color: var(--text-dim); }

  /* ── Responsive ────────────────────── */
  @media (max-width: 768px) {
    .topbar { gap: 16px; padding: 12px 16px; }
    .topbar-stat .value { font-size: 18px; }
    .container { padding: 12px; }
    .bot-grid { grid-template-columns: 1fr; }
  }

  /* Refresh indicator */
  .refresh-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
    flex-shrink: 0;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }
</style>
</head>
<body>

<!-- Top Bar -->
<div class="topbar">
  <div class="topbar-title">Polymarket Bot v2</div>
  <div class="refresh-dot"></div>
  <div class="topbar-stat">
    <span class="label">Total P&amp;L</span>
    <span class="value mono" id="ov-pnl">--</span>
  </div>
  <div class="topbar-stat">
    <span class="label">Bankroll</span>
    <span class="value mono" id="ov-bankroll">--</span>
  </div>
  <div class="topbar-stat">
    <span class="label">Win Rate</span>
    <span class="value mono" id="ov-winrate">--</span>
  </div>
  <div class="topbar-stat">
    <span class="label">Trades</span>
    <span class="value mono" id="ov-trades">--</span>
  </div>
  <div class="spacer"></div>
  <div id="ov-mode" class="mode-badge mode-paper">--</div>
</div>

<div class="container">

  <!-- Bot Cards -->
  <div class="section-title">Active Bots</div>
  <div class="bot-grid" id="bot-grid"></div>

  <!-- Signals Table -->
  <div class="section-title">Live Signals</div>
  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Bot</th><th>Price</th><th>CVD</th><th>VWAP %</th>
          <th>Funding</th><th>RSI</th><th>BB %</th><th>Regime</th><th>Updated</th>
        </tr>
      </thead>
      <tbody id="signals-body"></tbody>
    </table>
  </div>

  <!-- Recent Trades -->
  <div class="section-title">Recent Trades</div>
  <div class="tbl-wrap" style="max-height:500px;overflow-y:auto;">
    <table>
      <thead>
        <tr>
          <th>Time</th><th>Bot</th><th>Signal</th><th>Entry</th>
          <th>Size</th><th>Outcome</th><th>P&amp;L</th>
        </tr>
      </thead>
      <tbody id="trades-body"></tbody>
    </table>
  </div>

  <!-- Risk Events -->
  <div class="section-title">Risk Events</div>
  <div class="risk-log" id="risk-log"></div>

</div>

<script>
// ── Helpers ─────────────────────────────────────────────
function fmt(n, d=2) {
  if (n == null) return '--';
  return Number(n).toFixed(d);
}
function fmtPnl(n) {
  if (n == null) return '--';
  const v = Number(n).toFixed(2);
  const prefix = n >= 0 ? '+$' : '-$';
  const abs = Math.abs(n).toFixed(2);
  return prefix + abs;
}
function pnlClass(n) {
  if (n == null) return '';
  return n >= 0 ? 'pnl-pos' : 'pnl-neg';
}
function sigArrow(sig) {
  if (!sig) return '<span class="signal-arrow signal-skip">--</span>';
  const up = sig.includes('YES') || sig === 'UP';
  const down = sig.includes('NO') || sig === 'DOWN';
  if (up) return '<span class="signal-arrow signal-up">&#9650;</span>';
  if (down) return '<span class="signal-arrow signal-down">&#9660;</span>';
  return '<span class="signal-arrow signal-skip">&#9644;</span>';
}
function confColor(c) {
  if (c >= 0.7) return 'var(--green)';
  if (c >= 0.4) return 'var(--gold)';
  return 'var(--red)';
}
function outcomeHtml(o) {
  if (!o) return '<span class="outcome-open">OPEN</span>';
  if (o === 'WIN') return '<span class="outcome-win">WIN</span>';
  return '<span class="outcome-loss">LOSS</span>';
}
function shortTime(ts) {
  if (!ts) return '--';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  } catch { return ts.slice(11,19); }
}
function colorNum(val, lo, hi) {
  if (val == null) return '<span class="mono" style="color:var(--text-dim)">--</span>';
  const n = Number(val);
  let c = 'var(--text)';
  if (n <= lo) c = 'var(--red)';
  else if (n >= hi) c = 'var(--green)';
  return `<span class="mono" style="color:${c}">${fmt(n,4)}</span>`;
}

// ── Render Functions ────────────────────────────────────
function renderOverview(d) {
  const pnlEl = document.getElementById('ov-pnl');
  pnlEl.textContent = fmtPnl(d.total_pnl);
  pnlEl.className = 'value mono ' + pnlClass(d.total_pnl);

  document.getElementById('ov-bankroll').textContent = '$' + fmt(d.total_bankroll);
  document.getElementById('ov-winrate').textContent = fmt(d.win_rate,1) + '%';
  document.getElementById('ov-trades').textContent = d.total_trades;

  const modeEl = document.getElementById('ov-mode');
  modeEl.textContent = d.mode.toUpperCase();
  modeEl.className = 'mode-badge mode-' + d.mode;
}

function renderBots(bots) {
  const grid = document.getElementById('bot-grid');
  grid.innerHTML = bots.map(b => `
    <div class="bot-card">
      <div class="bot-header">
        <span class="bot-badge">${b.strategy}/${b.asset}</span>
        ${sigArrow(b.signal)}
      </div>
      <div class="confidence-bar-wrap">
        <div class="confidence-bar" style="width:${(b.confidence*100).toFixed(0)}%;background:${confColor(b.confidence)}"></div>
      </div>
      <div class="bot-stats">
        <div class="bot-stat-item"><span class="lbl">Win Rate</span><span class="val mono">${fmt(b.win_rate,1)}%</span></div>
        <div class="bot-stat-item"><span class="lbl">P&L</span><span class="val mono ${pnlClass(b.total_pnl)}">${fmtPnl(b.total_pnl)}</span></div>
        <div class="bot-stat-item"><span class="lbl">Bankroll</span><span class="val mono">$${fmt(b.bankroll)}</span></div>
        <div class="bot-stat-item"><span class="lbl">Trades</span><span class="val mono">${b.trades}${b.open ? ' <span style="color:var(--gold)">(' + b.open + ' open)</span>' : ''}</span></div>
      </div>
    </div>
  `).join('');
}

function renderSignals(signals) {
  const body = document.getElementById('signals-body');
  body.innerHTML = signals.map(s => `<tr>
    <td class="mono" style="font-weight:600">${s.strategy}/${s.asset}</td>
    <td class="mono">${s.price != null ? '$'+fmt(s.price,2) : '--'}</td>
    <td>${colorNum(s.cvd, -500000, 500000)}</td>
    <td>${colorNum(s.vwap_change, -0.001, 0.001)}</td>
    <td>${colorNum(s.funding_rate, -0.0001, 0.0001)}</td>
    <td class="mono" style="color:${s.rsi!=null?(s.rsi<30?'var(--green)':s.rsi>70?'var(--red)':'var(--text)'):'var(--text-dim)'}">${s.rsi != null ? fmt(s.rsi,1) : '--'}</td>
    <td class="mono" style="color:${s.bb_pct!=null?(s.bb_pct<0.2?'var(--green)':s.bb_pct>0.8?'var(--red)':'var(--text)'):'var(--text-dim)'}">${s.bb_pct != null ? fmt(s.bb_pct,3) : '--'}</td>
    <td><span style="color:var(--purple);font-weight:600">${s.regime || '--'}</span></td>
    <td style="color:var(--text-dim);font-size:12px">${shortTime(s.updated_at)}</td>
  </tr>`).join('');
}

function renderTrades(trades) {
  const body = document.getElementById('trades-body');
  body.innerHTML = trades.map(t => `<tr>
    <td style="color:var(--text-dim);font-size:12px">${shortTime(t.timestamp)}</td>
    <td class="mono" style="font-weight:600">${t.strategy}/${t.asset}</td>
    <td>${sigArrow(t.signal)} <span class="mono" style="font-size:12px">${t.signal||'--'}</span></td>
    <td class="mono">${fmt(t.entry_price,4)}</td>
    <td class="mono">$${fmt(t.bet_size,2)}</td>
    <td>${outcomeHtml(t.outcome)}</td>
    <td class="mono ${pnlClass(t.pnl)}">${t.pnl != null ? fmtPnl(t.pnl) : '--'}</td>
  </tr>`).join('');
}

function renderRisk(events) {
  const log = document.getElementById('risk-log');
  if (!events.length) {
    log.innerHTML = '<div class="risk-row"><span class="risk-detail">No risk events recorded.</span></div>';
    return;
  }
  log.innerHTML = events.map(e => `
    <div class="risk-row">
      <span class="risk-time mono">${shortTime(e.timestamp)}</span>
      <span class="risk-type">${e.event_type}</span>
      <span class="risk-detail">${e.strategy||''}${e.asset?' / '+e.asset:''} ${e.details||''}</span>
    </div>
  `).join('');
}

// ── Fetch & Refresh ─────────────────────────────────────
async function refresh() {
  try {
    const [overview, bots, signals, trades, risk] = await Promise.all([
      fetch('/api/overview').then(r => r.json()),
      fetch('/api/bots').then(r => r.json()),
      fetch('/api/signals').then(r => r.json()),
      fetch('/api/trades?limit=50').then(r => r.json()),
      fetch('/api/risk-events').then(r => r.json()),
    ]);
    renderOverview(overview);
    renderBots(bots);
    renderSignals(signals);
    renderTrades(trades);
    renderRisk(risk);
  } catch (err) {
    console.error('Refresh failed:', err);
  }
}

// Initial load + auto-refresh every 5s
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""
