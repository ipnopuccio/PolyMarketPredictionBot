"""Trade resolver -- async loop that checks for resolved markets.

Polls Gamma API every 30 seconds, resolves open trades, updates bankroll.

Phase 14: Uses a single shared httpx.AsyncClient for the lifetime of the
resolver loop (no connection-per-fetch leak).  Stale trades (open > 6 h)
are moved to the dead-letter queue automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from bot.config import settings
from bot.core.events import EventBus
from bot.core.retry import with_retry
from bot.monitoring.metrics import (
    PNL_PER_TRADE,
    PNL_TOTAL,
    RESOLVER_CYCLES,
    TRADES_RESOLVED,
)
from bot.core.types import ACTIVE_BOTS
from bot.storage.database import Database

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
DEAD_LETTER_AFTER_HOURS = 6  # move to DLQ after this many hours unresolved


class Resolver:
    """Resolves open trades by polling Polymarket for market settlement."""

    def __init__(self, db: Database, bus: EventBus) -> None:
        self._db = db
        self._bus = bus
        # Shared HTTP client — created in run(), None otherwise
        self._client: httpx.AsyncClient | None = None

    # ── Public API ──────────────────────────────────────────

    async def run(self) -> None:
        """Start the resolver loop (runs forever).

        A single httpx.AsyncClient is kept alive for the duration of the loop,
        reusing TCP connections instead of creating one per request.
        """
        logger.info("Resolver started -- checking every 30s")
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
        async with httpx.AsyncClient(timeout=10, limits=limits) as client:
            self._client = client
            try:
                while True:
                    try:
                        RESOLVER_CYCLES.inc()
                        await self._resolve_cycle()
                        await self._dlq_cycle()
                    except Exception as exc:
                        logger.error("Resolver cycle error: %s", exc, exc_info=True)
                    await asyncio.sleep(30)
            finally:
                self._client = None

    # ── Internals ───────────────────────────────────────────

    async def _resolve_cycle(self) -> None:
        open_trades = await self._db.get_open_trades()
        if not open_trades:
            return

        # Group by market_id to avoid duplicate API calls
        by_market: dict[str, list[dict]] = {}
        for trade in open_trades:
            mid = trade.get("market_id")
            if mid:
                by_market.setdefault(mid, []).append(trade)

        resolved_any = False
        for market_id, trades in by_market.items():
            market = await self._fetch_market(market_id)
            if market is None:
                continue

            resolution = self._infer_resolution(market)
            if resolution is None:
                continue

            for trade in trades:
                outcome, pnl = self._calculate_pnl(
                    trade["signal"], trade["entry_price"],
                    trade["bet_size"], resolution,
                    taker_fee_pct=settings.fees.taker_fee_pct,
                )
                await self._db.resolve_trade(trade["id"], outcome, pnl)
                resolved_any = True

                # Prometheus metrics
                TRADES_RESOLVED.labels(
                    strategy=trade["strategy"], asset=trade["asset"], outcome=outcome,
                ).inc()
                PNL_PER_TRADE.labels(
                    strategy=trade["strategy"], asset=trade["asset"],
                ).observe(pnl)
                PNL_TOTAL.labels(
                    strategy=trade["strategy"], asset=trade["asset"],
                ).inc(pnl)

                icon = "+" if outcome == "WIN" else "-"
                logger.info(
                    "[%s/%s] #%d %s %s  P&L: $%s%.2f",
                    trade["strategy"], trade["asset"], trade["id"],
                    icon, outcome, "+" if pnl >= 0 else "", pnl,
                )

                await self._bus.publish("trade.resolved", {
                    "trade_id": trade["id"],
                    "strategy": trade["strategy"],
                    "asset": trade["asset"],
                    "outcome": outcome,
                    "pnl": pnl,
                    "signal": trade["signal"],
                    "entry_price": trade["entry_price"],
                    "bet_size": trade["bet_size"],
                    "market_id": market_id,
                })

        # Publish aggregated metrics after resolutions
        if resolved_any:
            await self._publish_metrics()

    async def _dlq_cycle(self) -> None:
        """Move trades that have been open for too long to the dead-letter queue."""
        try:
            stale = await self._db.get_stale_open_trades(
                older_than_hours=DEAD_LETTER_AFTER_HOURS
            )
            for trade in stale:
                await self._db.move_to_dead_letter(
                    trade["id"],
                    reason=f"Unresolved after {DEAD_LETTER_AFTER_HOURS}h",
                )
                logger.warning(
                    "[DLQ] Trade #%d (%s/%s) moved to dead-letter queue "
                    "(open > %dh)",
                    trade["id"], trade["strategy"], trade["asset"],
                    DEAD_LETTER_AFTER_HOURS,
                )
                await self._bus.publish("trade.dead_letter", {
                    "trade_id": trade["id"],
                    "strategy": trade["strategy"],
                    "asset": trade["asset"],
                    "market_id": trade.get("market_id"),
                    "reason": f"Unresolved after {DEAD_LETTER_AFTER_HOURS}h",
                })
        except Exception as exc:
            logger.debug("DLQ cycle error: %s", exc)

    async def _publish_metrics(self) -> None:
        """Publish aggregated portfolio metrics to the metrics WS channel."""
        try:
            strategies = list({s for s, _ in ACTIVE_BOTS})
            assets = list({a for _, a in ACTIVE_BOTS})
            all_stats = await self._db.get_all_stats(strategies, assets)

            total_pnl = sum(s.get("total_pnl", 0) for s in all_stats)
            total_bankroll = sum(s.get("bankroll", 0) for s in all_stats)
            total_trades = sum(s.get("trades", 0) for s in all_stats)
            total_wins = sum(s.get("wins", 0) for s in all_stats)
            win_rate = (total_wins / total_trades * 100) if total_trades else 0

            await self._bus.publish("metrics.updated", {
                "total_pnl": round(total_pnl, 2),
                "total_bankroll": round(total_bankroll, 2),
                "total_trades": total_trades,
                "win_rate": round(win_rate, 1),
            })
        except Exception as e:
            logger.debug("Failed to publish metrics: %s", e)

    @with_retry(max_attempts=3, base_delay=1.0)
    async def _fetch_market_with_retry(self, market_id: str) -> dict:
        """Fetch a single market from Gamma API (retriable)."""
        assert self._client is not None
        resp = await self._client.get(f"{GAMMA_API}/markets/{market_id}")
        resp.raise_for_status()
        return resp.json()

    async def _fetch_market(self, market_id: str) -> dict | None:
        if self._client is None:
            return None
        try:
            return await self._fetch_market_with_retry(market_id)
        except Exception as exc:
            logger.warning("Failed to fetch market %s: %s", market_id, exc)
            return None

    @staticmethod
    def _infer_resolution(market: dict) -> str | None:
        """Determine the market resolution.

        Polymarket sets closed=True but resolution may stay null.
        Infer from outcomePrices: ["1","0"] = Up won, ["0","1"] = Down won.
        """
        if market.get("resolution") is not None:
            return market["resolution"]
        if not market.get("closed"):
            return None

        raw = market.get("outcomePrices", [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return None
        try:
            prices = [float(p) for p in raw]
        except (TypeError, ValueError):
            return None

        if len(prices) < 2:
            return None
        if prices[0] >= 0.99:
            return "1"  # Up won
        if prices[1] >= 0.99:
            return "0"  # Down won
        return None  # not yet settled

    @staticmethod
    def _calculate_pnl(
        signal: str, entry_price: float, bet_size: float, resolution: str,
        taker_fee_pct: float = 0.02,
    ) -> tuple[str, float]:
        """Compute outcome and P&L for a resolved trade.

        WIN  P&L =  (1 - entry_price) * bet_size - taker_fee
        LOSS P&L = -entry_price * bet_size
        """
        yes_won = resolution == "1"
        bet_on_yes = signal == "BUY_YES"
        won = (yes_won and bet_on_yes) or (not yes_won and not bet_on_yes)

        if won:
            gross_profit = (1 - entry_price) * bet_size
            fee = gross_profit * taker_fee_pct
            return "WIN", gross_profit - fee
        return "LOSS", -entry_price * bet_size
