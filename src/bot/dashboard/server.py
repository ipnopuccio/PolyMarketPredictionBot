"""Authenticated REST API for the Polymarket Bot v2 dashboard.

Endpoints:
    GET  /api/v2/positions  — open positions
    GET  /api/v2/pnl        — P&L breakdown
    GET  /api/v2/params     — current strategy parameters
    POST /api/v2/params     — hot-update parameters (no restart)
    GET  /api/v2/logs       — recent log entries
    GET  /api/v2/health     — bot / VPN / DB / feed health
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from bot.config import settings
from bot.core.types import ACTIVE_BOTS
from bot.dashboard.auth import verify_api_key
from bot.dashboard.log_buffer import LogBuffer
from bot.network.vpn_guard import is_vpn_active

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", dependencies=[Depends(verify_api_key)], tags=["Dashboard"])


# ── GET /positions ───────────────────────────────────────
@router.get("/positions")
async def get_positions(request: Request) -> list[dict]:
    """Open positions across all active bots."""
    db = request.app.state.db
    trades = await db.get_open_trades()
    bot_keys = {(s, a) for s, a in ACTIVE_BOTS}
    return [t for t in trades if (t["strategy"], t["asset"]) in bot_keys]


# ── GET /pnl ─────────────────────────────────────────────
@router.get("/pnl")
async def get_pnl(request: Request) -> dict:
    """P&L realizzato e non, per bot e totale."""
    db = request.app.state.db
    result: dict[str, Any] = {
        "total_realized_pnl": 0.0,
        "total_unrealized_cost": 0.0,
        "bots": [],
    }

    for strat, asset in ACTIVE_BOTS:
        stats = await db.get_stats(strat, asset)
        bankroll = await db.get_bankroll(strat, asset)
        open_trades = await db.get_open_trades(strat, asset)
        unrealized = sum(
            t["entry_price"] * t["bet_size"] for t in open_trades
        )
        realized = stats.get("total_pnl", 0.0)
        result["total_realized_pnl"] += realized
        result["total_unrealized_cost"] += unrealized

        result["bots"].append({
            "strategy": strat,
            "asset": asset,
            "realized_pnl": round(realized, 2),
            "unrealized_cost": round(unrealized, 2),
            "bankroll": round(bankroll, 2),
            "trades": stats.get("trades", 0),
            "open": len(open_trades),
            "win_rate": stats.get("win_rate", 0.0),
        })

    result["total_realized_pnl"] = round(result["total_realized_pnl"], 2)
    result["total_unrealized_cost"] = round(result["total_unrealized_cost"], 2)
    return result


# ── GET /params ──────────────────────────────────────────
@router.get("/params")
async def get_params() -> dict:
    """Current strategy and risk parameters."""
    return {
        "mode": settings.mode,
        "initial_bankroll": settings.initial_bankroll,
        "momentum": settings.momentum.model_dump(),
        "bollinger": settings.bollinger.model_dump(),
        "turbo_cvd": settings.turbo_cvd.model_dump(),
        "turbo_vwap": settings.turbo_vwap.model_dump(),
        "risk": settings.risk.model_dump(),
        "sizer": settings.sizer.model_dump(),
        "fees": settings.fees.model_dump(),
    }


# ── POST /params ─────────────────────────────────────────
@router.post("/params")
async def update_params(updates: dict[str, Any]) -> dict:
    """Update parameters at runtime (no restart required).

    Example body::

        {
            "momentum": {"cvd_threshold": 500000},
            "risk": {"strategy_drawdown_disable": 0.30}
        }
    """
    config_map: dict[str, Any] = {
        "momentum": settings.momentum,
        "bollinger": settings.bollinger,
        "turbo_cvd": settings.turbo_cvd,
        "turbo_vwap": settings.turbo_vwap,
        "risk": settings.risk,
        "sizer": settings.sizer,
        "fees": settings.fees,
    }

    applied: list[str] = []
    errors: list[str] = []

    for section, values in updates.items():
        cfg = config_map.get(section)
        if cfg is None:
            errors.append(f"Unknown section: {section}")
            continue
        if not isinstance(values, dict):
            errors.append(f"{section}: expected dict of key/value pairs")
            continue
        for key, value in values.items():
            if not hasattr(cfg, key):
                errors.append(f"{section}.{key}: unknown parameter")
                continue
            try:
                setattr(cfg, key, value)
                applied.append(f"{section}.{key}={value}")
            except Exception as exc:
                errors.append(f"{section}.{key}: {exc}")

    if applied:
        logger.info("Params updated via API: %s", applied)

    return {"applied": applied, "errors": errors}


# ── GET /logs ────────────────────────────────────────────
@router.get("/logs")
async def get_logs(
    n: int = Query(default=100, le=500),
    level: str | None = Query(default=None),
) -> list[dict]:
    """Last N log entries, optionally filtered by level."""
    buf = LogBuffer.get()
    if buf is None:
        return []
    return buf.get_entries(n=n, level=level)


# ── GET /health ──────────────────────────────────────────
@router.get("/health")
async def get_health(request: Request) -> dict:
    """Bot health: database, VPN, feed freshness."""
    db = request.app.state.db

    # DB check
    db_ok = False
    try:
        async with db.conn.execute("SELECT 1") as cur:
            await cur.fetchone()
        db_ok = True
    except Exception:
        pass

    # VPN check
    vpn_ok = await is_vpn_active()

    # Feed freshness: are signal_states updated recently?
    feeds_ok = False
    try:
        states = await db.get_signal_states()
        if states:
            latest = max(
                (s.get("updated_at", "") for s in states), default=""
            )
            if latest:
                last_ts = datetime.fromisoformat(latest)
                age = (datetime.now(timezone.utc) - last_ts).total_seconds()
                feeds_ok = age < 120  # healthy if < 2 min old
    except Exception:
        pass

    all_ok = db_ok and vpn_ok and feeds_ok
    return {
        "status": "healthy" if all_ok else "degraded",
        "mode": settings.mode,
        "components": {
            "database": "ok" if db_ok else "error",
            "vpn": "active" if vpn_ok else "inactive",
            "feeds": "ok" if feeds_ok else "stale",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── GET /exchanges ──────────────────────────────────────
@router.get("/exchanges")
async def get_exchanges(request: Request) -> dict:
    """Per-exchange health: latency, errors, connection status."""
    exchange_mgr = getattr(request.app.state, "exchange_mgr", None)
    if exchange_mgr is None:
        return {"total": 0, "healthy": 0, "exchanges": []}

    return exchange_mgr.summary()
