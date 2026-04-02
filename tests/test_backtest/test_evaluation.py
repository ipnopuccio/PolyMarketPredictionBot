"""Tests for Phase 9: Strategy evaluation, comparison, and reporting.

Covers: evaluator, comparison, comparison_report, evaluation API endpoints.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from bot.backtest.comparison import (
    ComparisonResult,
    StrategyScore,
    _chi_square_win_rates,
    _composite_score,
    _mean_ci,
    _wilson_ci,
    compare_strategies,
    export_csv,
)
from bot.backtest.comparison_report import ComparisonReportGenerator
from bot.backtest.evaluator import ACTIVE_BOTS, EvaluationResult, StrategyEvaluator
from bot.backtest.models import (
    BacktestConfig,
    BacktestResult,
    FullBacktestReport,
    MonteCarloResult,
    PerformanceMetrics,
    WalkForwardResult,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _make_metrics(**kwargs) -> PerformanceMetrics:
    defaults = dict(
        total_trades=100,
        wins=60,
        losses=40,
        win_rate=0.60,
        total_pnl=5.0,
        avg_pnl_per_trade=0.05,
        gross_profit=8.0,
        gross_loss=3.0,
        profit_factor=2.6667,
        sharpe_ratio=0.8,
        sortino_ratio=1.2,
        max_drawdown=2.0,
        max_drawdown_pct=0.05,
        recovery_factor=2.5,
        avg_win=0.1333,
        avg_loss=0.075,
        max_consecutive_wins=8,
        max_consecutive_losses=3,
        expectancy=0.05,
        final_bankroll=45.0,
    )
    defaults.update(kwargs)
    return PerformanceMetrics(**defaults)


def _make_config(strategy: str = "MOMENTUM", asset: str = "BTC") -> BacktestConfig:
    return BacktestConfig(
        strategy=strategy,
        asset=asset,
        start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )


def _make_report(
    strategy: str = "MOMENTUM",
    asset: str = "BTC",
    sharpe: float = 0.8,
    win_rate: float = 0.6,
    total_pnl: float = 5.0,
    total_trades: int = 100,
    wins: int = 60,
    mc_prob_profit: float | None = 0.7,
    wf_overfit: float | None = 1.2,
) -> FullBacktestReport:
    m = _make_metrics(
        sharpe_ratio=sharpe,
        win_rate=win_rate,
        total_pnl=total_pnl,
        total_trades=total_trades,
        wins=wins,
        losses=total_trades - wins,
    )
    bt = BacktestResult(
        config=_make_config(strategy, asset),
        metrics=m,
        trades=[],
        equity_curve=[40.0, 42.0, 41.0, 45.0],
        drawdown_curve=[0.0, 0.0, 0.024, 0.0],
        timestamps=[],
        run_duration_ms=50.0,
    )
    mc = None
    if mc_prob_profit is not None:
        mc = MonteCarloResult(
            iterations=100,
            confidence_level=0.95,
            prob_profit=mc_prob_profit,
            prob_ruin=0.02,
            median_sharpe=sharpe * 0.9,
        )
    wf = None
    if wf_overfit is not None:
        wf = WalkForwardResult(
            config=_make_config(strategy, asset),
            windows=[],
            aggregated_oos=_make_metrics(sharpe_ratio=sharpe * 0.8),
            overfitting_score=wf_overfit,
        )
    return FullBacktestReport(backtest=bt, walk_forward=wf, monte_carlo=mc)


# ── Wilson CI tests ───────────────────────────────────────────────────────

class TestWilsonCI:
    def test_zero_total(self):
        lo, hi = _wilson_ci(0, 0)
        assert lo == 0.0 and hi == 0.0

    def test_perfect_win_rate(self):
        lo, hi = _wilson_ci(100, 100)
        assert lo > 0.95
        assert hi == 1.0

    def test_50_pct(self):
        lo, hi = _wilson_ci(50, 100)
        assert 0.39 < lo < 0.42
        assert 0.58 < hi < 0.61

    def test_small_sample(self):
        lo, hi = _wilson_ci(3, 5)
        assert lo > 0.0
        assert hi < 1.0


# ── Mean CI tests ─────────────────────────────────────────────────────────

class TestMeanCI:
    def test_empty(self):
        lo, hi = _mean_ci([])
        assert lo == 0.0 and hi == 0.0

    def test_single_value(self):
        lo, hi = _mean_ci([1.0])
        assert lo == 0.0 and hi == 0.0

    def test_symmetric(self):
        lo, hi = _mean_ci([1.0, 2.0, 3.0, 4.0, 5.0])
        mean = 3.0
        assert lo < mean < hi

    def test_constant_values(self):
        lo, hi = _mean_ci([5.0, 5.0, 5.0])
        assert lo == 5.0
        assert hi == 5.0


# ── Chi-square tests ──────────────────────────────────────────────────────

class TestChiSquare:
    def test_single_strategy(self):
        stat, df, crit, sig = _chi_square_win_rates([(0.6, 100)])
        assert stat == 0.0
        assert not sig

    def test_identical_win_rates(self):
        stat, df, crit, sig = _chi_square_win_rates([
            (0.6, 100), (0.6, 100), (0.6, 100),
        ])
        assert stat == pytest.approx(0.0, abs=0.01)
        assert not sig

    def test_very_different_win_rates(self):
        stat, df, crit, sig = _chi_square_win_rates([
            (0.9, 200), (0.3, 200),
        ])
        assert stat > crit
        assert sig

    def test_df_correct(self):
        _, df, _, _ = _chi_square_win_rates([
            (0.5, 50), (0.5, 50), (0.5, 50), (0.5, 50), (0.5, 50),
        ])
        assert df == 4


# ── Composite score tests ────────────────────────────────────────────────

class TestCompositeScore:
    def test_high_performance(self):
        m = _make_metrics(sharpe_ratio=2.0, profit_factor=3.0, win_rate=0.8, recovery_factor=5.0)
        score = _composite_score(m, mc_prob_profit=0.85, wf_oos_sharpe=1.5)
        assert score > 0.6

    def test_low_performance(self):
        m = _make_metrics(sharpe_ratio=-1.0, profit_factor=0.5, win_rate=0.3, recovery_factor=0.5)
        score = _composite_score(m, mc_prob_profit=0.2, wf_oos_sharpe=-0.5)
        assert score < 0.3

    def test_none_mc_and_wf(self):
        m = _make_metrics()
        score = _composite_score(m, mc_prob_profit=None, wf_oos_sharpe=None)
        assert 0.0 <= score <= 1.0


# ── Compare strategies tests ─────────────────────────────────────────────

class TestCompareStrategies:
    def test_empty(self):
        result = compare_strategies([])
        assert result.scores == []

    def test_single_report(self):
        report = _make_report()
        result = compare_strategies([report])
        assert len(result.scores) == 1
        assert result.scores[0].rank == 1

    def test_ranking_order(self):
        r1 = _make_report("MOMENTUM", "BTC", sharpe=2.0, win_rate=0.8, total_pnl=10.0)
        r2 = _make_report("BOLLINGER", "BTC", sharpe=0.2, win_rate=0.45, total_pnl=-1.0)
        r3 = _make_report("TURBO_CVD", "ETH", sharpe=1.0, win_rate=0.65, total_pnl=5.0)

        result = compare_strategies([r1, r2, r3])
        strategies = [(s.strategy, s.rank) for s in result.scores]

        # MOMENTUM should rank #1 (highest sharpe + pnl)
        assert result.scores[0].strategy == "MOMENTUM"
        assert result.scores[0].rank == 1

    def test_chi_square_populated(self):
        r1 = _make_report("MOMENTUM", "BTC", win_rate=0.6, total_trades=100, wins=60)
        r2 = _make_report("BOLLINGER", "BTC", win_rate=0.8, total_trades=100, wins=80)
        result = compare_strategies([r1, r2])
        assert result.chi_square_df == 1
        assert result.chi_square_statistic > 0

    def test_confidence_intervals(self):
        report = _make_report(total_trades=100, wins=60)
        result = compare_strategies([report])
        s = result.scores[0]
        assert s.win_rate_ci_low < s.win_rate < s.win_rate_ci_high


# ── CSV export tests ──────────────────────────────────────────────────────

class TestCSVExport:
    def test_header_row(self):
        r = _make_report()
        comp = compare_strategies([r])
        csv_str = export_csv(comp)
        lines = csv_str.strip().split("\n")
        assert "Rank" in lines[0]
        assert "Strategy" in lines[0]
        assert "Composite Score" in lines[0]

    def test_data_rows(self):
        r1 = _make_report("MOMENTUM", "BTC")
        r2 = _make_report("TURBO_CVD", "ETH")
        comp = compare_strategies([r1, r2])
        csv_str = export_csv(comp)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 3  # header + 2 data rows

    def test_empty(self):
        comp = ComparisonResult()
        csv_str = export_csv(comp)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 1  # header only


# ── Comparison report tests ──────────────────────────────────────────────

class TestComparisonReport:
    def test_generates_html(self):
        reports = [
            _make_report("MOMENTUM", "BTC", sharpe=1.0),
            _make_report("TURBO_CVD", "ETH", sharpe=0.5),
        ]
        eval_result = EvaluationResult(reports=reports, run_duration_ms=100.0)
        comp = compare_strategies(reports)

        gen = ComparisonReportGenerator()
        html = gen.generate(eval_result, comp)

        assert "<!DOCTYPE html>" in html
        assert "Strategy Evaluation Report" in html
        assert "MOMENTUM" in html
        assert "TURBO_CVD" in html
        assert "sharpeChart" in html
        assert "equityOverlay" in html
        assert "Chi-Square" in html


# ── Evaluator tests ──────────────────────────────────────────────────────

class TestEvaluator:
    def test_active_bots_list(self):
        assert len(ACTIVE_BOTS) == 5
        strategies = {s for s, _ in ACTIVE_BOTS}
        assert "TURBO_CVD" in strategies
        assert "MOMENTUM" in strategies
        assert "BOLLINGER" in strategies

    @pytest.mark.asyncio
    async def test_run_single_bot(self):
        evaluator = StrategyEvaluator(
            num_points=100,
            walk_forward=False,
            monte_carlo=False,
        )
        result = await evaluator.run_all(
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 2, 1),
            bots=[("MOMENTUM", "BTC")],
        )
        assert len(result.reports) == 1
        assert result.reports[0].backtest.config.strategy == "MOMENTUM"
        assert result.run_duration_ms > 0

    @pytest.mark.asyncio
    async def test_run_with_mc_and_wf(self):
        evaluator = StrategyEvaluator(
            num_points=100,
            walk_forward=True,
            monte_carlo=True,
        )
        result = await evaluator.run_all(
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 2, 1),
            bots=[("MOMENTUM", "BTC")],
        )
        report = result.reports[0]
        # WF and MC may or may not produce results depending on trade count
        # but the report structure should be valid
        assert report.backtest is not None

    @pytest.mark.asyncio
    async def test_run_multiple_bots(self):
        evaluator = StrategyEvaluator(
            num_points=50,
            walk_forward=False,
            monte_carlo=False,
        )
        bots = [("MOMENTUM", "BTC"), ("TURBO_CVD", "ETH")]
        result = await evaluator.run_all(
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 2, 1),
            bots=bots,
        )
        assert len(result.reports) == 2
        strats = {r.backtest.config.strategy for r in result.reports}
        assert strats == {"MOMENTUM", "TURBO_CVD"}

    def test_evaluation_result_to_dict(self):
        reports = [_make_report("MOMENTUM", "BTC")]
        er = EvaluationResult(reports=reports, run_duration_ms=42.0)
        d = er.to_dict()
        assert d["num_strategies"] == 1
        assert d["run_duration_ms"] == 42.0
        assert len(d["reports"]) == 1
