"""Unified trade executor -- handles both scaling and dynamic modes.

Scaling mode:  multiple orders per window (up to max_orders_per_window).
Dynamic mode:  one order per window per market (idempotent).

All pre-trade checks are centralised here:
  bankroll, window capacity, elapsed %, market availability, idempotency, risk.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import time

from bot.config import StrategyConfig, settings
from bot.core.events import EventBus
from bot.core.types import Signal, SignalResult, MarketInfo
from bot.execution.risk import RiskManager
from bot.execution.sizer import Sizer
from bot.market.finder import MarketFinder
from bot.monitoring.metrics import (
    BET_SIZE,
    EXECUTION_CHECKS_FAILED,
    EXECUTION_LATENCY,
    TRADES_TOTAL,
)
from bot.network.vpn_guard import is_vpn_active
from bot.storage.database import Database

logger = logging.getLogger(__name__)

# Interval per asset (minutes) -- used by window tracker
_INTERVAL_MIN = {"BTC": 5, "ETH": 5, "SOL": 15}
_MAX_STALE_KEYS = 500


class _WindowTracker:
    """In-memory per-window order counter (replaces v1 window_tracker module)."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str, str], int] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _window_key(asset: str) -> str:
        interval = _INTERVAL_MIN.get(asset, 5)
        now = datetime.now(timezone.utc)
        minute_floor = (now.minute // interval) * interval
        return now.strftime(f"%Y-%m-%dT%H:{minute_floor:02d}")

    def _cleanup(self) -> None:
        if len(self._counts) <= _MAX_STALE_KEYS:
            return
        current = {self._window_key(a) for a in _INTERVAL_MIN}
        stale = [k for k in self._counts if k[2] not in current]
        for k in stale:
            del self._counts[k]

    async def can_place(self, strategy: str, asset: str, max_orders: int) -> bool:
        async with self._lock:
            key = (strategy, asset, self._window_key(asset))
            return self._counts.get(key, 0) < max_orders

    async def record(self, strategy: str, asset: str) -> int:
        """Record an order and return the new count for this window."""
        async with self._lock:
            key = (strategy, asset, self._window_key(asset))
            self._counts[key] = self._counts.get(key, 0) + 1
            self._cleanup()
            return self._counts[key]

    async def count(self, strategy: str, asset: str) -> int:
        async with self._lock:
            key = (strategy, asset, self._window_key(asset))
            return self._counts.get(key, 0)


class Executor:
    """Unified executor for all strategy modes."""

    def __init__(
        self,
        db: Database,
        market_finder: MarketFinder,
        sizer: Sizer,
        risk: RiskManager,
        bus: EventBus,
    ) -> None:
        self._db = db
        self._mf = market_finder
        self._sizer = sizer
        self._risk = risk
        self._bus = bus
        self._tracker = _WindowTracker()

    async def execute(
        self,
        result: SignalResult,
        strategy_cfg: StrategyConfig,
        *,
        scaling: bool = True,
        size_multiplier: float = 1.0,
    ) -> int | None:
        """Place a trade if all checks pass.

        Args:
            result: Strategy signal output.
            strategy_cfg: Configuration for the strategy that produced the signal.
            scaling: If True allow multiple orders per window (scaling mode).
                     If False allow only one order per market per window (dynamic).
            size_multiplier: Regime-based multiplier (Phase 12.3), e.g. 0.5 for volatile.

        Returns:
            Trade ID on success, None if skipped.
        """
        strategy = result.strategy
        asset = result.asset
        signal = result.signal

        if signal == Signal.SKIP:
            return None

        t0 = time.monotonic()

        # 0. VPN guard
        if not await is_vpn_active():
            logger.warning("[%s/%s] VPN inactive — order blocked", strategy, asset)
            EXECUTION_CHECKS_FAILED.labels(check_name="vpn", strategy=strategy, asset=asset).inc()
            await self._db.log_risk_event(
                "VPN_BLOCKED", strategy, asset, "No VPN tunnel detected",
            )
            return None

        # 1. Bankroll pre-check
        bankroll = await self._db.get_bankroll(strategy, asset)
        if bankroll < 1.0:
            logger.debug("[%s/%s] Bankroll too low (%.2f)", strategy, asset, bankroll)
            return None

        # 2. Window capacity
        max_orders = strategy_cfg.max_orders_per_window
        if not await self._tracker.can_place(strategy, asset, max_orders):
            logger.debug("[%s/%s] Window capacity reached", strategy, asset)
            return None

        # 3. Elapsed % check
        elapsed = self._mf.window_elapsed_pct(asset)
        if elapsed > strategy_cfg.max_elapsed_pct:
            logger.debug(
                "[%s/%s] Window %.0f%% elapsed (max %.0f%%)",
                strategy, asset, elapsed * 100, strategy_cfg.max_elapsed_pct * 100,
            )
            return None

        # 4. Risk checks
        if not await self._risk.check_all(self._db, strategy, asset):
            return None

        # 5. Market availability
        market = await self._mf.find_market(asset)
        if market is None:
            return None

        # 6. Idempotency (dynamic mode: one order per market per window)
        if not scaling:
            open_trades = await self._db.get_open_trades(strategy, asset)
            if any(t["market_id"] == market.market_id for t in open_trades):
                logger.debug(
                    "[%s/%s] Already open on market %s", strategy, asset, market.market_id,
                )
                return None

        # 7. Entry price
        entry_price = await self._mf.get_entry_price(market, signal)
        # Apply slippage (simulates realistic fill price)
        slippage = settings.fees.slippage_bps / 10000
        entry_price = entry_price * (1 + slippage)
        if entry_price <= 0 or entry_price >= 1:
            logger.warning("[%s/%s] Invalid entry price %.4f", strategy, asset, entry_price)
            return None

        # 7b. Strategy entry-price guard (max_entry_buy_yes / max_entry_buy_no)
        max_entry = getattr(
            strategy_cfg,
            "max_entry_buy_yes" if signal == Signal.BUY_YES else "max_entry_buy_no",
            1.0,
        )
        if entry_price > max_entry:
            logger.debug(
                "[%s/%s] Entry %.4f > max %.4f — skipped",
                strategy, asset, entry_price, max_entry,
            )
            return None

        # 8. Position size (with regime-based multiplier from Phase 12.3)
        bet_size = await self._sizer.kelly_size(self._db, strategy, asset, bankroll)
        if size_multiplier != 1.0:
            bet_size = max(1.0, bet_size * size_multiplier)

        # 9. Build snapshot dict from the FeedSnapshot
        snapshot = result.snapshot.to_dict()

        # 9b. Build comprehensive indicators JSON for post-mortem analysis
        full_indicators = {
            "rsi": result.indicators.get("rsi"),
            "bb_pct": result.indicators.get("bb_pct"),
            "cvd": snapshot.get("cvd_2min"),
            "vwap_change": snapshot.get("vwap_change"),
            "funding_rate": snapshot.get("funding_rate"),
            "open_interest": snapshot.get("open_interest"),
            "long_short_ratio": snapshot.get("long_short_ratio"),
            "book_imbalance": snapshot.get("book_imbalance"),
            "liq_long_2min": snapshot.get("liq_long_2min"),
            "liq_short_2min": snapshot.get("liq_short_2min"),
            "regime": result.indicators.get("regime", "UNKNOWN"),
            "confidence": result.confidence,
        }

        # 10. Insert trade
        trade_id = await self._db.reserve_and_insert_trade(
            strategy=strategy,
            asset=asset,
            market_id=market.market_id,
            signal=signal.value,
            entry_price=entry_price,
            bet_size=bet_size,
            confidence=result.confidence,
            regime=result.indicators.get("regime", "UNKNOWN") or "UNKNOWN",
            snapshot=snapshot,
            rsi=result.indicators.get("rsi"),
            bb_pct=result.indicators.get("bb_pct"),
            indicators_json=json.dumps(full_indicators),
        )

        if trade_id is None:
            logger.warning(
                "[%s/%s] Trade insertion failed (insufficient bankroll?)", strategy, asset,
            )
            return None

        # Deduct gas fee from bankroll
        await self._db.deduct_fee(strategy, asset, settings.fees.gas_per_trade)

        # 11. Track window order count
        order_num = await self._tracker.record(strategy, asset)
        mode = "SCALE" if scaling else "DYNAMIC"
        logger.info(
            "[%s/%s] %s %s @ %.3f  size=$%.2f  #%d/%d  id=%d",
            strategy, asset, mode, signal.value,
            entry_price, bet_size, order_num, max_orders, trade_id,
        )

        # 12. Record metrics
        elapsed_ms = (time.monotonic() - t0) * 1000
        EXECUTION_LATENCY.labels(strategy=strategy).observe(elapsed_ms)
        TRADES_TOTAL.labels(strategy=strategy, asset=asset, signal=signal.value).inc()
        BET_SIZE.labels(strategy=strategy, asset=asset).observe(bet_size)

        # 13. Publish event
        await self._bus.publish("trade.placed", {
            "trade_id": trade_id,
            "strategy": strategy,
            "asset": asset,
            "signal": signal.value,
            "entry_price": entry_price,
            "bet_size": bet_size,
            "market_id": market.market_id,
            "confidence": result.confidence,
        })

        return trade_id
