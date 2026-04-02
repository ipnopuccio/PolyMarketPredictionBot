"""Strategy evaluator — runs all active strategies through backtest + analysis.

Orchestrates BacktestEngine, WalkForwardAnalyzer, and MonteCarloAnalyzer
across every active strategy/asset combination and collects results for
comparative analysis.

Active bots (from v1 validation):
    1. TURBO_CVD  / ETH
    2. TURBO_VWAP / ETH
    3. MOMENTUM   / BTC
    4. MOMENTUM   / SOL
    5. BOLLINGER  / BTC
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from bot.backtest.engine import BacktestEngine
from bot.backtest.models import (
    BacktestConfig,
    FullBacktestReport,
)
from bot.backtest.monte_carlo import MonteCarloAnalyzer
from bot.backtest.walk_forward import WalkForwardAnalyzer
from bot.core.types import ACTIVE_BOTS

logger = logging.getLogger(__name__)


class EvaluationResult:
    """Container for a full multi-strategy evaluation run."""

    __slots__ = ("reports", "run_duration_ms")

    def __init__(
        self,
        reports: list[FullBacktestReport],
        run_duration_ms: float,
    ) -> None:
        self.reports = reports
        self.run_duration_ms = run_duration_ms

    def to_dict(self) -> dict:
        return {
            "reports": [r.model_dump(mode="json") for r in self.reports],
            "run_duration_ms": round(self.run_duration_ms, 1),
            "num_strategies": len(self.reports),
        }


class StrategyEvaluator:
    """Run all active strategy/asset pairs through backtest + analysis.

    Example::

        evaluator = StrategyEvaluator()
        result = await evaluator.run_all(
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 3, 1),
        )
    """

    def __init__(
        self,
        num_points: int = 2_000,
        walk_forward: bool = True,
        monte_carlo: bool = True,
        seed: int | None = 42,
    ) -> None:
        self.num_points = num_points
        self.walk_forward = walk_forward
        self.monte_carlo = monte_carlo
        self.seed = seed

    async def run_all(
        self,
        start_date: datetime,
        end_date: datetime,
        initial_bankroll: float = 40.0,
        bots: list[tuple[str, str]] | None = None,
    ) -> EvaluationResult:
        """Backtest every active strategy/asset pair and collect results.

        Args:
            start_date: Backtest start date.
            end_date: Backtest end date.
            initial_bankroll: Starting capital per strategy.
            bots: Override default ACTIVE_BOTS list.

        Returns:
            EvaluationResult containing a FullBacktestReport per strategy.
        """
        t0 = time.perf_counter()
        pairs = bots or ACTIVE_BOTS
        engine = BacktestEngine()
        wf_analyzer = WalkForwardAnalyzer() if self.walk_forward else None
        mc_analyzer = MonteCarloAnalyzer() if self.monte_carlo else None

        reports: list[FullBacktestReport] = []

        for strategy, asset in pairs:
            config = BacktestConfig(
                strategy=strategy,
                asset=asset,
                start_date=start_date,
                end_date=end_date,
                initial_bankroll=initial_bankroll,
            )

            logger.info("Evaluating %s / %s ...", strategy, asset)

            # Core backtest
            bt_result = await engine.run(
                config,
                num_points=self.num_points,
                seed=self.seed,
            )

            # Walk-forward
            wf_result = None
            if wf_analyzer:
                try:
                    wf_result = await wf_analyzer.run(config, engine)
                except Exception as exc:
                    logger.warning(
                        "Walk-forward failed for %s/%s: %s", strategy, asset, exc
                    )

            # Monte Carlo
            mc_result = None
            if mc_analyzer and len(bt_result.trades) >= 2:
                try:
                    mc_result = mc_analyzer.run(
                        trades=bt_result.trades,
                        config=config,
                        seed=self.seed,
                    )
                except Exception as exc:
                    logger.warning(
                        "Monte Carlo failed for %s/%s: %s", strategy, asset, exc
                    )

            reports.append(
                FullBacktestReport(
                    backtest=bt_result,
                    walk_forward=wf_result,
                    monte_carlo=mc_result,
                )
            )

            logger.info(
                "  %s/%s: %d trades, win_rate=%.1f%%, sharpe=%.3f, pnl=$%.4f",
                strategy,
                asset,
                bt_result.metrics.total_trades,
                bt_result.metrics.win_rate * 100,
                bt_result.metrics.sharpe_ratio,
                bt_result.metrics.total_pnl,
            )

        duration_ms = (time.perf_counter() - t0) * 1_000
        logger.info(
            "Evaluation complete: %d strategies in %.0f ms", len(reports), duration_ms
        )

        return EvaluationResult(reports=reports, run_duration_ms=duration_ms)
