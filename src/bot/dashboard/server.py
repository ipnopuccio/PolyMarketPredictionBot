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

from bot.strategies.selector import StrategySelector

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


# ── GET /equity ────────────────────────────────────────
@router.get("/equity")
async def get_equity(
    request: Request,
    strategy: str | None = Query(None),
    asset: str | None = Query(None),
    days: int = Query(7, ge=1, le=90),
) -> list[dict]:
    """Equity curve time series, optionally filtered by strategy/asset."""
    import time
    db = request.app.state.db
    since_ts = time.time() - (days * 86400)
    return await db.get_equity_curve(strategy=strategy, asset=asset, since_ts=since_ts)


# ── GET /trades/{trade_id}/indicators ──────────────────
@router.get("/trades/{trade_id}/indicators")
async def get_trade_indicators(request: Request, trade_id: int) -> dict:
    """Full indicator snapshot at trade time for post-mortem analysis."""
    db = request.app.state.db
    indicators = await db.get_trade_indicators(trade_id)
    if indicators is None:
        return {"trade_id": trade_id, "indicators": None}
    return {"trade_id": trade_id, "indicators": indicators}


# ── Phase 12.6: Strategy Hot-Swap ──────────────────────

@router.post("/strategies/enable")
async def enable_strategy(request: Request, body: dict[str, Any]) -> dict:
    """Enable a strategy via manual override (no restart required).

    Body: {"strategy": "MOMENTUM"}
    """
    selector: StrategySelector | None = getattr(request.app.state, "selector", None)
    if selector is None:
        return {"error": "Strategy selector not initialized"}

    strategy_name = body.get("strategy", "").upper()
    if not strategy_name:
        return {"error": "Missing 'strategy' field"}

    selector.set_override(strategy_name, True)
    logger.info("[HotSwap] Strategy %s manually ENABLED", strategy_name)

    # Audit log
    db = request.app.state.db
    await db.log_risk_event("STRATEGY_ENABLED", strategy_name, "", f"Manual override via API")

    return {"strategy": strategy_name, "enabled": True, "override": True}


@router.post("/strategies/disable")
async def disable_strategy(request: Request, body: dict[str, Any]) -> dict:
    """Disable a strategy via manual override (no restart required).

    Body: {"strategy": "MOMENTUM"}
    """
    selector: StrategySelector | None = getattr(request.app.state, "selector", None)
    if selector is None:
        return {"error": "Strategy selector not initialized"}

    strategy_name = body.get("strategy", "").upper()
    if not strategy_name:
        return {"error": "Missing 'strategy' field"}

    selector.set_override(strategy_name, False)
    logger.info("[HotSwap] Strategy %s manually DISABLED", strategy_name)

    db = request.app.state.db
    await db.log_risk_event("STRATEGY_DISABLED", strategy_name, "", f"Manual override via API")

    return {"strategy": strategy_name, "enabled": False, "override": True}


@router.post("/strategies/reset")
async def reset_strategy_override(request: Request, body: dict[str, Any]) -> dict:
    """Clear manual override, revert to regime-based rules.

    Body: {"strategy": "MOMENTUM"} or {"all": true}
    """
    selector: StrategySelector | None = getattr(request.app.state, "selector", None)
    if selector is None:
        return {"error": "Strategy selector not initialized"}

    if body.get("all"):
        selector.clear_all_overrides()
        logger.info("[HotSwap] All strategy overrides cleared")
        return {"cleared": "all"}

    strategy_name = body.get("strategy", "").upper()
    if not strategy_name:
        return {"error": "Missing 'strategy' field"}

    selector.clear_override(strategy_name)
    logger.info("[HotSwap] Override cleared for %s", strategy_name)
    return {"strategy": strategy_name, "override": None}


# ── Phase 14: Deep Health Check ─────────────────────────
@router.get("/health/deep")
async def get_health_deep(request: Request) -> dict:
    """Comprehensive health check: feeds, exchanges, DB, resolver, DLQ.

    Returns a status of HEALTHY / DEGRADED / UNHEALTHY plus per-component
    details and a Prometheus-friendly numeric ``health_score`` (0/1/2).
    """
    import time

    db = request.app.state.db
    exchange_mgr = getattr(request.app.state, "exchange_mgr", None)
    components: dict[str, dict] = {}
    issues: list[str] = []

    # ── Database ──────────────────────────────────────────
    try:
        t0 = time.monotonic()
        async with db.conn.execute("SELECT 1") as cur:
            await cur.fetchone()
        db_latency_ms = (time.monotonic() - t0) * 1000
        components["database"] = {"status": "ok", "latency_ms": round(db_latency_ms, 1)}
    except Exception as exc:
        components["database"] = {"status": "error", "error": str(exc)}
        issues.append("database unreachable")

    # ── Feed staleness per asset ───────────────────────────
    try:
        states = await db.get_signal_states()
        feed_info: dict[str, dict] = {}
        for s in states:
            asset = s.get("asset", "?")
            updated_at = s.get("updated_at", "")
            if updated_at:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)).total_seconds()
                stale = age > 120
                feed_info[asset] = {"age_s": round(age, 1), "stale": stale}
                if stale:
                    issues.append(f"feed stale: {asset} ({age:.0f}s ago)")
            else:
                feed_info[asset] = {"age_s": None, "stale": True}
                issues.append(f"feed never updated: {asset}")
        components["feeds"] = {"status": "ok" if not any(v["stale"] for v in feed_info.values()) else "stale", "assets": feed_info}
    except Exception as exc:
        components["feeds"] = {"status": "error", "error": str(exc)}
        issues.append("feed check failed")

    # ── Exchange health ────────────────────────────────────
    if exchange_mgr is not None:
        summary = exchange_mgr.summary()
        healthy_ratio = summary.get("healthy", 0) / max(summary.get("total", 1), 1)
        components["exchanges"] = {
            "status": "ok" if healthy_ratio >= 0.5 else "degraded",
            "healthy": summary.get("healthy", 0),
            "total": summary.get("total", 0),
        }
        if healthy_ratio < 0.5:
            issues.append(f"only {summary.get('healthy')}/{summary.get('total')} exchanges healthy")
    else:
        components["exchanges"] = {"status": "unavailable"}

    # ── Open trades & DLQ ─────────────────────────────────
    try:
        open_trades = await db.get_open_trades()
        dlq = await db.get_dead_letter_trades(limit=10)
        components["trades"] = {
            "open": len(open_trades),
            "dead_letter_queue": len(dlq),
        }
        if dlq:
            issues.append(f"{len(dlq)} trades in dead-letter queue")
    except Exception as exc:
        components["trades"] = {"status": "error", "error": str(exc)}

    # ── VPN ───────────────────────────────────────────────
    from bot.network.vpn_guard import is_vpn_active
    vpn_ok = await is_vpn_active()
    components["vpn"] = {"status": "active" if vpn_ok else "inactive"}

    # ── Overall status ────────────────────────────────────
    db_ok = components.get("database", {}).get("status") == "ok"
    feeds_ok = components.get("feeds", {}).get("status") == "ok"

    if not db_ok or len(issues) >= 3:
        overall = "UNHEALTHY"
        health_score = 0
    elif issues:
        overall = "DEGRADED"
        health_score = 1
    else:
        overall = "HEALTHY"
        health_score = 2

    return {
        "status": overall,
        "health_score": health_score,
        "issues": issues,
        "components": components,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Phase 14: Dead Letter Queue endpoint ─────────────────
@router.get("/dead-letter")
async def get_dead_letter(
    request: Request,
    limit: int = Query(default=50, le=200),
) -> list[dict]:
    """Dead-letter trades: unresolved after 6h, pending manual review."""
    db = request.app.state.db
    return await db.get_dead_letter_trades(limit=limit)


@router.post("/dead-letter/{dlq_id}/resolve")
async def resolve_dead_letter(
    request: Request,
    dlq_id: int,
    body: dict[str, str],
) -> dict:
    """Manually resolve a dead-letter trade.

    Body: ``{"outcome": "WIN" | "LOSS" | "VOID"}``
    """
    outcome = body.get("outcome", "").upper()
    if outcome not in ("WIN", "LOSS", "VOID"):
        return {"error": "outcome must be WIN, LOSS, or VOID"}
    db = request.app.state.db
    await db.resolve_dead_letter(dlq_id, outcome)
    logger.info("[DLQ] Manually resolved dlq_id=%d as %s", dlq_id, outcome)
    return {"dlq_id": dlq_id, "resolved_outcome": outcome}


@router.get("/strategies/status")
async def get_strategies_status(request: Request) -> dict:
    """Current strategy enable/disable status with regime info."""
    selector: StrategySelector | None = getattr(request.app.state, "selector", None)
    if selector is None:
        return {"error": "Strategy selector not initialized"}

    # Get current regime from the latest signal state
    from bot.core.types import RegimeType
    db = request.app.state.db
    states = await db.get_signal_states()
    current_regime = RegimeType.UNKNOWN
    if states:
        regime_str = states[0].get("regime", "UNKNOWN")
        try:
            current_regime = RegimeType(regime_str)
        except ValueError:
            pass

    status = selector.get_status(current_regime)
    return {
        "regime": current_regime.value,
        "strategies": status,
    }
