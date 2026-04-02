"""Statistical comparison across strategy backtest results.

Provides:
  - Sharpe-based ranking
  - Chi-square test on win rates (are differences statistically significant?)
  - Confidence intervals for win rate and P&L per trade
  - Composite scoring (weighted Sharpe + Sortino + PF + MC prob_profit)
  - CSV export
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from bot.backtest.models import FullBacktestReport, PerformanceMetrics


# ── Chi-square critical values (df → alpha=0.05) ──────────────────────────
# For k strategies the df = k-1.  We only need a few.
_CHI2_CRITICAL_005 = {
    1: 3.841,
    2: 5.991,
    3: 7.815,
    4: 9.488,
    5: 11.070,
    6: 12.592,
    7: 14.067,
    8: 15.507,
}


@dataclass
class StrategyScore:
    """Ranked entry for one strategy/asset pair."""
    rank: int
    strategy: str
    asset: str
    sharpe: float
    sortino: float
    win_rate: float
    total_pnl: float
    profit_factor: float
    max_drawdown_pct: float
    total_trades: int
    expectancy: float
    # Confidence intervals (95%)
    win_rate_ci_low: float = 0.0
    win_rate_ci_high: float = 0.0
    pnl_per_trade_ci_low: float = 0.0
    pnl_per_trade_ci_high: float = 0.0
    # Monte Carlo
    mc_prob_profit: float | None = None
    mc_prob_ruin: float | None = None
    mc_median_sharpe: float | None = None
    # Walk-forward
    wf_overfitting_score: float | None = None
    wf_oos_sharpe: float | None = None
    # Composite score
    composite_score: float = 0.0


@dataclass
class ComparisonResult:
    """Full comparison output."""
    scores: list[StrategyScore] = field(default_factory=list)
    chi_square_statistic: float = 0.0
    chi_square_df: int = 0
    chi_square_critical: float = 0.0
    chi_square_significant: bool = False


def compare_strategies(reports: Sequence[FullBacktestReport]) -> ComparisonResult:
    """Run full statistical comparison across strategy reports.

    Args:
        reports: List of FullBacktestReport, one per strategy/asset.

    Returns:
        ComparisonResult with ranked scores and chi-square test.
    """
    if not reports:
        return ComparisonResult()

    scores: list[StrategyScore] = []

    for report in reports:
        bt = report.backtest
        m = bt.metrics
        cfg = bt.config

        # 95% confidence interval for win rate (Wilson score interval)
        wr_low, wr_high = _wilson_ci(m.wins, m.total_trades)

        # 95% CI for P&L per trade (t-distribution approximation)
        pnl_values = [t.pnl for t in bt.trades]
        pnl_ci_low, pnl_ci_high = _mean_ci(pnl_values)

        # Monte Carlo fields
        mc_prob_profit = None
        mc_prob_ruin = None
        mc_median_sharpe = None
        if report.monte_carlo:
            mc_prob_profit = report.monte_carlo.prob_profit
            mc_prob_ruin = report.monte_carlo.prob_ruin
            mc_median_sharpe = report.monte_carlo.median_sharpe

        # Walk-forward fields
        wf_overfit = None
        wf_oos_sharpe = None
        if report.walk_forward:
            wf_overfit = report.walk_forward.overfitting_score
            wf_oos_sharpe = report.walk_forward.aggregated_oos.sharpe_ratio

        # Composite score
        composite = _composite_score(m, mc_prob_profit, wf_oos_sharpe)

        scores.append(StrategyScore(
            rank=0,  # assigned after sorting
            strategy=cfg.strategy,
            asset=cfg.asset,
            sharpe=m.sharpe_ratio,
            sortino=m.sortino_ratio,
            win_rate=m.win_rate,
            total_pnl=m.total_pnl,
            profit_factor=m.profit_factor,
            max_drawdown_pct=m.max_drawdown_pct,
            total_trades=m.total_trades,
            expectancy=m.expectancy,
            win_rate_ci_low=wr_low,
            win_rate_ci_high=wr_high,
            pnl_per_trade_ci_low=pnl_ci_low,
            pnl_per_trade_ci_high=pnl_ci_high,
            mc_prob_profit=mc_prob_profit,
            mc_prob_ruin=mc_prob_ruin,
            mc_median_sharpe=mc_median_sharpe,
            wf_overfitting_score=wf_overfit,
            wf_oos_sharpe=wf_oos_sharpe,
            composite_score=composite,
        ))

    # Sort by composite score descending, assign ranks
    scores.sort(key=lambda s: s.composite_score, reverse=True)
    for i, s in enumerate(scores):
        s.rank = i + 1

    # Chi-square test on win rates
    chi_stat, chi_df, chi_crit, chi_sig = _chi_square_win_rates(
        [(s.win_rate, s.total_trades) for s in scores]
    )

    return ComparisonResult(
        scores=scores,
        chi_square_statistic=round(chi_stat, 4),
        chi_square_df=chi_df,
        chi_square_critical=chi_crit,
        chi_square_significant=chi_sig,
    )


def export_csv(result: ComparisonResult) -> str:
    """Export comparison results as CSV string.

    Returns:
        CSV-formatted string with one row per strategy.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    header = [
        "Rank", "Strategy", "Asset", "Composite Score",
        "Sharpe", "Sortino", "Win Rate", "Win Rate CI Low", "Win Rate CI High",
        "Total P&L", "Avg P&L/Trade CI Low", "Avg P&L/Trade CI High",
        "Profit Factor", "Max DD %", "Total Trades", "Expectancy",
        "MC Prob Profit", "MC Prob Ruin", "MC Median Sharpe",
        "WF Overfit Score", "WF OOS Sharpe",
    ]
    writer.writerow(header)

    for s in result.scores:
        writer.writerow([
            s.rank, s.strategy, s.asset, round(s.composite_score, 4),
            s.sharpe, s.sortino,
            round(s.win_rate, 4), round(s.win_rate_ci_low, 4), round(s.win_rate_ci_high, 4),
            s.total_pnl,
            round(s.pnl_per_trade_ci_low, 6), round(s.pnl_per_trade_ci_high, 6),
            s.profit_factor, round(s.max_drawdown_pct, 4),
            s.total_trades, s.expectancy,
            s.mc_prob_profit if s.mc_prob_profit is not None else "",
            s.mc_prob_ruin if s.mc_prob_ruin is not None else "",
            s.mc_median_sharpe if s.mc_median_sharpe is not None else "",
            s.wf_overfitting_score if s.wf_overfitting_score is not None else "",
            s.wf_oos_sharpe if s.wf_oos_sharpe is not None else "",
        ])

    return buf.getvalue()


# ── Statistical helpers ────────────────────────────────────────────────────


def _wilson_ci(
    wins: int, total: int, z: float = 1.96
) -> tuple[float, float]:
    """Wilson score 95% confidence interval for a proportion.

    More accurate than the normal approximation for small samples
    and extreme proportions (near 0 or 1).
    """
    if total == 0:
        return 0.0, 0.0
    p = wins / total
    denom = 1 + z ** 2 / total
    centre = (p + z ** 2 / (2 * total)) / denom
    spread = z * math.sqrt((p * (1 - p) + z ** 2 / (4 * total)) / total) / denom
    return round(max(0.0, centre - spread), 4), round(min(1.0, centre + spread), 4)


def _mean_ci(
    values: Sequence[float], z: float = 1.96
) -> tuple[float, float]:
    """95% confidence interval for the mean of a sample."""
    if len(values) < 2:
        return 0.0, 0.0
    arr = np.array(values, dtype=np.float64)
    mean = float(np.mean(arr))
    se = float(np.std(arr, ddof=1)) / math.sqrt(len(arr))
    return round(mean - z * se, 6), round(mean + z * se, 6)


def _chi_square_win_rates(
    pairs: list[tuple[float, int]],
) -> tuple[float, int, float, bool]:
    """Chi-square test for homogeneity of win rates across strategies.

    H0: All strategies have the same underlying win rate.
    H1: At least one strategy's win rate differs significantly.

    Args:
        pairs: List of (win_rate, total_trades) per strategy.

    Returns:
        (chi2_statistic, degrees_of_freedom, critical_value, is_significant)
    """
    k = len(pairs)
    if k < 2:
        return 0.0, 0, 0.0, False

    # Compute pooled win rate
    total_wins = sum(wr * n for wr, n in pairs)
    total_n = sum(n for _, n in pairs)
    if total_n == 0:
        return 0.0, k - 1, 0.0, False
    pooled_wr = total_wins / total_n

    chi2 = 0.0
    for wr, n in pairs:
        if n == 0:
            continue
        observed_wins = wr * n
        expected_wins = pooled_wr * n
        expected_losses = (1 - pooled_wr) * n
        observed_losses = n - observed_wins

        if expected_wins > 0:
            chi2 += (observed_wins - expected_wins) ** 2 / expected_wins
        if expected_losses > 0:
            chi2 += (observed_losses - expected_losses) ** 2 / expected_losses

    df = k - 1
    critical = _CHI2_CRITICAL_005.get(df, 3.841)
    significant = chi2 > critical

    return chi2, df, critical, significant


def _composite_score(
    m: PerformanceMetrics,
    mc_prob_profit: float | None,
    wf_oos_sharpe: float | None,
) -> float:
    """Weighted composite score for strategy ranking.

    Components (normalized to ~[0,1] range):
      - Sharpe ratio:     30%  (clamped [-2, 4])
      - Profit factor:    20%  (clamped [0, 5])
      - Win rate:         15%
      - MC prob profit:   15%  (if available, else use win rate proxy)
      - WF OOS Sharpe:    10%  (if available, else use backtest Sharpe)
      - Recovery factor:  10%  (clamped [0, 10])
    """
    # Normalize Sharpe to [0, 1] from range [-2, 4]
    sharpe_norm = max(0.0, min(1.0, (m.sharpe_ratio + 2) / 6))

    # Normalize profit factor to [0, 1] from range [0, 5]
    pf = min(m.profit_factor, 5.0)
    pf_norm = pf / 5.0

    # Win rate is already [0, 1]
    wr_norm = m.win_rate

    # MC prob profit [0, 1]
    mc_norm = mc_prob_profit if mc_prob_profit is not None else m.win_rate

    # WF OOS Sharpe normalized same as backtest Sharpe
    wf_sharpe = wf_oos_sharpe if wf_oos_sharpe is not None else m.sharpe_ratio
    wf_norm = max(0.0, min(1.0, (wf_sharpe + 2) / 6))

    # Recovery factor normalized from [0, 10]
    rf = min(m.recovery_factor, 10.0)
    rf_norm = rf / 10.0

    score = (
        0.30 * sharpe_norm
        + 0.20 * pf_norm
        + 0.15 * wr_norm
        + 0.15 * mc_norm
        + 0.10 * wf_norm
        + 0.10 * rf_norm
    )

    return round(score, 4)
