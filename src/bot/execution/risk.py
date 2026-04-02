"""Risk management layer using RiskConfig.

Three independent checks:
  1. Drawdown  -- strategy-level and portfolio-level
  2. Correlation -- limits same-direction exposure per asset
  3. Circuit breaker -- pauses after consecutive losses
"""
from __future__ import annotations

import logging
import time

from bot.config import RiskConfig
from bot.storage.database import Database

logger = logging.getLogger(__name__)


class RiskManager:
    """Portfolio- and strategy-level risk gate."""

    def __init__(self, cfg: RiskConfig) -> None:
        self._cfg = cfg
        # circuit breaker cooldown: (strategy, asset) -> resume_ts
        self._cb_cooldown: dict[tuple[str, str], float] = {}

    # ── Convenience: run all checks ─────────────────────────

    async def check_all(self, db: Database, strategy: str, asset: str) -> bool:
        """Return True if the trade is allowed, False to block."""
        if not await self.check_drawdown(db, strategy, asset):
            return False
        if not await self.check_correlation(db, asset):
            return False
        if not await self.check_circuit_breaker(db, strategy, asset):
            return False
        return True

    # ── 1. Drawdown ─────────────────────────────────────────

    async def check_drawdown(self, db: Database, strategy: str, asset: str) -> bool:
        """Check strategy-level and portfolio-level drawdown limits.

        Returns True if trading is allowed.
        """
        cfg = self._cfg

        # Strategy-level drawdown
        current = await db.get_bankroll(strategy, asset)
        peak = await db.get_bankroll_peak(strategy, asset)
        if peak > 0:
            dd = (peak - current) / peak
            if dd >= cfg.strategy_drawdown_disable:
                logger.warning(
                    "[RISK] %s/%s drawdown %.1f%% >= disable threshold %.1f%% -- BLOCKED",
                    strategy, asset, dd * 100, cfg.strategy_drawdown_disable * 100,
                )
                await db.log_risk_event(
                    "DRAWDOWN_DISABLE", strategy, asset,
                    f"dd={dd:.3f} peak={peak:.2f} current={current:.2f}",
                )
                return False

        # Portfolio-level drawdown (aggregate across all strategies for this asset)
        all_bankrolls = await db.get_all_bankrolls()
        portfolio_current = sum(
            b["current"] for b in all_bankrolls if b["asset"] == asset
        )
        portfolio_peak = sum(
            b["peak"] for b in all_bankrolls if b["asset"] == asset
        )
        if portfolio_peak > 0:
            pdd = (portfolio_peak - portfolio_current) / portfolio_peak
            if pdd >= cfg.portfolio_drawdown_pause:
                logger.warning(
                    "[RISK] Portfolio %s drawdown %.1f%% >= pause threshold -- BLOCKED",
                    asset, pdd * 100,
                )
                await db.log_risk_event(
                    "PORTFOLIO_DRAWDOWN_PAUSE", None, asset,
                    f"pdd={pdd:.3f} peak={portfolio_peak:.2f} current={portfolio_current:.2f}",
                )
                return False

        return True

    # ── 2. Correlation ──────────────────────────────────────

    async def check_correlation(self, db: Database, asset: str) -> bool:
        """Block if too many open trades in the same direction for this asset.

        Returns True if trading is allowed.
        """
        cfg = self._cfg
        counts = await db.count_open_by_direction(asset)
        total_open = counts["BUY_YES"] + counts["BUY_NO"]

        # Max same-direction per asset
        if counts["BUY_YES"] >= cfg.max_same_direction_per_asset:
            logger.debug(
                "[RISK] %s has %d open BUY_YES (max %d)",
                asset, counts["BUY_YES"], cfg.max_same_direction_per_asset,
            )
            # Only block if trying to add more of the dominant side
            # (checked at executor level based on signal, but we can still cap)

        if counts["BUY_NO"] >= cfg.max_same_direction_per_asset:
            logger.debug(
                "[RISK] %s has %d open BUY_NO (max %d)",
                asset, counts["BUY_NO"], cfg.max_same_direction_per_asset,
            )

        # Unidirectional exposure %
        if total_open > 0:
            dominant = max(counts["BUY_YES"], counts["BUY_NO"])
            uni_pct = dominant / total_open
            if uni_pct > cfg.max_unidirectional_exposure_pct and total_open >= 3:
                logger.warning(
                    "[RISK] %s unidirectional exposure %.0f%% (max %.0f%%) -- BLOCKED",
                    asset, uni_pct * 100, cfg.max_unidirectional_exposure_pct * 100,
                )
                await db.log_risk_event(
                    "CORRELATION_BLOCK", None, asset,
                    f"yes={counts['BUY_YES']} no={counts['BUY_NO']} uni={uni_pct:.2f}",
                )
                return False

        return True

    # ── 3. Circuit breaker ──────────────────────────────────

    async def check_circuit_breaker(self, db: Database, strategy: str, asset: str) -> bool:
        """Pause trading after consecutive losses.

        Returns True if trading is allowed.
        """
        cfg = self._cfg
        key = (strategy, asset)

        # Check if in cooldown
        resume_ts = self._cb_cooldown.get(key)
        if resume_ts is not None:
            if time.time() < resume_ts:
                logger.debug(
                    "[RISK] %s/%s circuit breaker active for %ds more",
                    strategy, asset, int(resume_ts - time.time()),
                )
                return False
            # Cooldown expired
            del self._cb_cooldown[key]

        # Check for consecutive losses
        outcomes = await db.get_recent_outcomes(
            strategy, asset, n=cfg.circuit_breaker_consecutive_losses,
        )
        if (
            len(outcomes) >= cfg.circuit_breaker_consecutive_losses
            and all(o == "LOSS" for o in outcomes)
        ):
            # Calculate cooldown: window_count * interval for the asset
            interval_s = {"BTC": 300, "ETH": 300, "SOL": 900}.get(asset, 300)
            cooldown_s = cfg.circuit_breaker_cooldown_windows * interval_s
            self._cb_cooldown[key] = time.time() + cooldown_s

            logger.warning(
                "[RISK] %s/%s circuit breaker triggered: %d consecutive losses. "
                "Pausing for %d windows (%ds)",
                strategy, asset, cfg.circuit_breaker_consecutive_losses,
                cfg.circuit_breaker_cooldown_windows, cooldown_s,
            )
            await db.log_risk_event(
                "CIRCUIT_BREAKER", strategy, asset,
                f"consecutive_losses={cfg.circuit_breaker_consecutive_losses} "
                f"cooldown={cooldown_s}s",
            )
            return False

        return True
