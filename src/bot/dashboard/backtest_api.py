"""Backtest and evaluation REST API endpoints for the Polymarket Bot v2 dashboard.

Endpoints:
    POST /api/v2/backtest           — Run a backtest, return JSON
    POST /api/v2/backtest/report    — Run a backtest, return HTML report
    POST /api/v2/evaluate           — Evaluate all strategies, return JSON
    POST /api/v2/evaluate/report    — Evaluate all strategies, return HTML report
    GET  /api/v2/evaluate/csv       — Evaluate all strategies, return CSV
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from bot.backtest.models import BacktestConfig, FullBacktestReport
from bot.backtest.report import ReportGenerator
from bot.dashboard.auth import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/backtest",
    dependencies=[Depends(verify_api_key)],
    tags=["Backtest & Evaluation"],
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _import_engine():
    """Lazy import of BacktestEngine to avoid circular dependencies at module load.

    Returns:
        The BacktestEngine class.

    Raises:
        HTTPException: 503 if the backtest package is not available.
    """
    try:
        from bot.backtest.engine import BacktestEngine  # noqa: PLC0415
        return BacktestEngine
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Backtest engine not available: {exc}",
        ) from exc


def _import_walk_forward():
    """Lazy import of WalkForwardAnalyzer.

    Returns:
        The WalkForwardAnalyzer class.

    Raises:
        HTTPException: 503 if the walk_forward module is not available.
    """
    try:
        from bot.backtest.walk_forward import WalkForwardAnalyzer  # noqa: PLC0415
        return WalkForwardAnalyzer
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Walk-forward analyzer not available: {exc}",
        ) from exc


def _import_monte_carlo():
    """Lazy import of MonteCarloAnalyzer.

    Returns:
        The MonteCarloAnalyzer class.

    Raises:
        HTTPException: 503 if the monte_carlo module is not available.
    """
    try:
        from bot.backtest.monte_carlo import MonteCarloAnalyzer  # noqa: PLC0415
        return MonteCarloAnalyzer
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Monte Carlo analyzer not available: {exc}",
        ) from exc


async def _run_full_backtest(
    request: Request,
    config: BacktestConfig,
    walk_forward: bool,
    monte_carlo: bool,
) -> FullBacktestReport:
    """Execute the backtest pipeline and assemble a FullBacktestReport.

    Runs the core backtest, then optionally appends walk-forward and/or
    Monte Carlo analyses based on the caller's flags.

    Args:
        request: The incoming FastAPI request (used to access app.state.db).
        config: Validated BacktestConfig specifying strategy, asset, and dates.
        walk_forward: Whether to run walk-forward analysis.
        monte_carlo: Whether to run Monte Carlo simulation.

    Returns:
        A FullBacktestReport containing the backtest result plus any optional
        analyses that were requested.

    Raises:
        HTTPException: 400 if the config is invalid.
        HTTPException: 503 if any required analyzer module is unavailable.
        HTTPException: 500 for unexpected engine errors.
    """
    db = request.app.state.db

    # ── Core backtest ────────────────────────────────────────────────────────
    BacktestEngine = _import_engine()
    try:
        engine = BacktestEngine()
        backtest_result = await engine.run(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Backtest engine error for %s/%s", config.strategy, config.asset)
        raise HTTPException(
            status_code=500,
            detail=f"Backtest engine error: {exc}",
        ) from exc

    # ── Walk-forward ─────────────────────────────────────────────────────────
    wf_result = None
    if walk_forward:
        WalkForwardAnalyzer = _import_walk_forward()
        try:
            wf_analyzer = WalkForwardAnalyzer()
            wf_result = await wf_analyzer.run(config, engine)
        except Exception as exc:
            logger.warning(
                "Walk-forward analysis failed for %s/%s: %s",
                config.strategy, config.asset, exc,
            )
            # Non-fatal: return report without walk-forward rather than 500

    # ── Monte Carlo ──────────────────────────────────────────────────────────
    mc_result = None
    if monte_carlo:
        MonteCarloAnalyzer = _import_monte_carlo()
        try:
            mc_analyzer = MonteCarloAnalyzer()
            mc_result = mc_analyzer.run(
                trades=backtest_result.trades,
                config=config,
            )
        except Exception as exc:
            logger.warning(
                "Monte Carlo analysis failed for %s/%s: %s",
                config.strategy, config.asset, exc,
            )
            # Non-fatal: return report without MC rather than 500

    return FullBacktestReport(
        backtest=backtest_result,
        walk_forward=wf_result,
        monte_carlo=mc_result,
    )


# ── POST /api/v2/backtest ────────────────────────────────────────────────────

@router.post("")
async def run_backtest(
    request: Request,
    config: BacktestConfig,
    walk_forward: bool = Query(default=False, description="Include walk-forward analysis"),
    monte_carlo: bool = Query(default=False, description="Include Monte Carlo simulation"),
) -> dict:
    """Run a full backtest and return results as JSON.

    Executes the backtest engine against historical data stored in the
    database, then optionally runs walk-forward and Monte Carlo analyses.

    Args:
        request: FastAPI request (provides access to app.state.db).
        config: Backtest parameters — strategy, asset, date range, fees, etc.
        walk_forward: If True, split data into IS/OOS windows and report
            overfitting score.
        monte_carlo: If True, randomize trade order across N iterations and
            report equity distribution and probability of ruin.

    Returns:
        JSON-serializable dict with keys:
            - ``backtest``: core BacktestResult fields
            - ``walk_forward``: WalkForwardResult or null
            - ``monte_carlo``: MonteCarloResult or null

    Raises:
        HTTPException 400: Invalid config (e.g. end_date before start_date).
        HTTPException 503: Backtest engine or analyzer not available.
        HTTPException 500: Unexpected engine error.
    """
    logger.info(
        "Backtest requested: strategy=%s asset=%s wf=%s mc=%s",
        config.strategy, config.asset, walk_forward, monte_carlo,
    )

    full_report = await _run_full_backtest(
        request=request,
        config=config,
        walk_forward=walk_forward,
        monte_carlo=monte_carlo,
    )

    return full_report.model_dump(mode="json")


# ── POST /api/v2/backtest/report ─────────────────────────────────────────────

@router.post("/report", response_class=HTMLResponse)
async def run_backtest_report(
    request: Request,
    config: BacktestConfig,
    walk_forward: bool = Query(default=False, description="Include walk-forward analysis"),
    monte_carlo: bool = Query(default=False, description="Include Monte Carlo simulation"),
) -> HTMLResponse:
    """Run a backtest and return a self-contained HTML report.

    Identical pipeline to ``POST /api/v2/backtest`` but renders the result
    as a dark-theme HTML document with Chart.js equity and drawdown charts,
    a sortable trade log, and optional walk-forward / Monte Carlo sections.

    Args:
        request: FastAPI request (provides access to app.state.db).
        config: Backtest parameters — strategy, asset, date range, fees, etc.
        walk_forward: If True, include walk-forward section in the report.
        monte_carlo: If True, include Monte Carlo section in the report.

    Returns:
        HTMLResponse containing a self-contained HTML document.

    Raises:
        HTTPException 400: Invalid config.
        HTTPException 503: Backtest engine or analyzer not available.
        HTTPException 500: Unexpected engine or render error.
    """
    logger.info(
        "Backtest HTML report requested: strategy=%s asset=%s wf=%s mc=%s",
        config.strategy, config.asset, walk_forward, monte_carlo,
    )

    full_report = await _run_full_backtest(
        request=request,
        config=config,
        walk_forward=walk_forward,
        monte_carlo=monte_carlo,
    )

    try:
        generator = ReportGenerator()
        html = generator.generate(full_report)
    except Exception as exc:
        logger.exception("HTML report render failed for %s/%s", config.strategy, config.asset)
        raise HTTPException(
            status_code=500,
            detail=f"Report render error: {exc}",
        ) from exc

    return HTMLResponse(content=html, status_code=200)


# ── Evaluation endpoints ─────────────────────────────────────────────────────

def _import_evaluator():
    """Lazy import of StrategyEvaluator."""
    try:
        from bot.backtest.evaluator import StrategyEvaluator  # noqa: PLC0415
        return StrategyEvaluator
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Strategy evaluator not available: {exc}",
        ) from exc


def _import_comparison():
    """Lazy import of comparison module."""
    try:
        from bot.backtest.comparison import compare_strategies, export_csv  # noqa: PLC0415
        return compare_strategies, export_csv
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Comparison module not available: {exc}",
        ) from exc


def _import_comparison_report():
    """Lazy import of ComparisonReportGenerator."""
    try:
        from bot.backtest.comparison_report import ComparisonReportGenerator  # noqa: PLC0415
        return ComparisonReportGenerator
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Comparison report not available: {exc}",
        ) from exc


class EvalRequest(BaseModel):
    """Request body for the evaluation endpoints."""
    start_date: datetime
    end_date: datetime
    initial_bankroll: float = 40.0
    num_points: int = 2000
    walk_forward: bool = True
    monte_carlo: bool = True


async def _run_evaluation(body: EvalRequest):
    """Shared evaluation runner for JSON + HTML + CSV endpoints."""
    StrategyEvaluator = _import_evaluator()
    compare_strategies, _ = _import_comparison()

    evaluator = StrategyEvaluator(
        num_points=body.num_points,
        walk_forward=body.walk_forward,
        monte_carlo=body.monte_carlo,
    )

    try:
        eval_result = await evaluator.run_all(
            start_date=body.start_date,
            end_date=body.end_date,
            initial_bankroll=body.initial_bankroll,
        )
    except Exception as exc:
        logger.exception("Evaluation engine error")
        raise HTTPException(status_code=500, detail=f"Evaluation error: {exc}") from exc

    comparison = compare_strategies(eval_result.reports)
    return eval_result, comparison


@router.post("/evaluate")
async def run_evaluation(body: EvalRequest) -> dict:
    """Evaluate all active strategies side-by-side, return JSON.

    Runs backtests for all 5 active strategy/asset pairs, computes
    comparative statistics (Sharpe ranking, Chi-square, confidence
    intervals), and returns the full results.
    """
    logger.info(
        "Evaluation requested: %s to %s, wf=%s mc=%s",
        body.start_date, body.end_date, body.walk_forward, body.monte_carlo,
    )

    eval_result, comparison = await _run_evaluation(body)

    return {
        "evaluation": eval_result.to_dict(),
        "comparison": {
            "scores": [
                {
                    "rank": s.rank,
                    "strategy": s.strategy,
                    "asset": s.asset,
                    "composite_score": s.composite_score,
                    "sharpe": s.sharpe,
                    "sortino": s.sortino,
                    "win_rate": s.win_rate,
                    "win_rate_ci": [s.win_rate_ci_low, s.win_rate_ci_high],
                    "total_pnl": s.total_pnl,
                    "profit_factor": s.profit_factor,
                    "max_drawdown_pct": s.max_drawdown_pct,
                    "total_trades": s.total_trades,
                    "expectancy": s.expectancy,
                    "mc_prob_profit": s.mc_prob_profit,
                    "mc_prob_ruin": s.mc_prob_ruin,
                    "wf_overfitting_score": s.wf_overfitting_score,
                    "wf_oos_sharpe": s.wf_oos_sharpe,
                }
                for s in comparison.scores
            ],
            "chi_square": {
                "statistic": comparison.chi_square_statistic,
                "df": comparison.chi_square_df,
                "critical_value": comparison.chi_square_critical,
                "significant": comparison.chi_square_significant,
            },
        },
    }


@router.post("/evaluate/report", response_class=HTMLResponse)
async def run_evaluation_report(body: EvalRequest) -> HTMLResponse:
    """Evaluate all strategies and return an HTML comparison report.

    Includes ranking table, Sharpe bar chart, equity overlay,
    Chi-square results, and per-strategy detail cards.
    """
    logger.info(
        "Evaluation HTML report requested: %s to %s",
        body.start_date, body.end_date,
    )

    eval_result, comparison = await _run_evaluation(body)

    ComparisonReportGenerator = _import_comparison_report()
    try:
        gen = ComparisonReportGenerator()
        html = gen.generate(eval_result, comparison)
    except Exception as exc:
        logger.exception("Evaluation report render failed")
        raise HTTPException(
            status_code=500, detail=f"Report render error: {exc}"
        ) from exc

    return HTMLResponse(content=html, status_code=200)


@router.post("/evaluate/csv")
async def run_evaluation_csv(body: EvalRequest):
    """Evaluate all strategies and return CSV export."""
    logger.info(
        "Evaluation CSV requested: %s to %s",
        body.start_date, body.end_date,
    )

    eval_result, comparison = await _run_evaluation(body)

    _, export_csv_fn = _import_comparison()
    csv_content = export_csv_fn(comparison)

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=strategy_evaluation.csv"},
    )
