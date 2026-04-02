"""Performance metrics calculator for backtesting.

All functions are pure — no I/O, no async, easily testable.
"""
from __future__ import annotations

import math

import numpy as np

from bot.backtest.models import PerformanceMetrics, SimulatedTrade


def compute_metrics(
    trades: list[SimulatedTrade],
    initial_bankroll: float,
) -> PerformanceMetrics:
    """Compute comprehensive performance metrics from a list of simulated trades."""
    if not trades:
        return PerformanceMetrics(final_bankroll=initial_bankroll)

    wins = [t for t in trades if t.exit_outcome == "WIN"]
    losses = [t for t in trades if t.exit_outcome == "LOSS"]

    total_trades = len(trades)
    n_wins = len(wins)
    n_losses = len(losses)
    win_rate = n_wins / total_trades if total_trades else 0.0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    total_pnl = gross_profit - gross_loss

    avg_win = gross_profit / n_wins if n_wins else 0.0
    avg_loss = gross_loss / n_losses if n_losses else 0.0
    avg_pnl = total_pnl / total_trades if total_trades else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    loss_rate = 1.0 - win_rate
    expectancy = avg_win * win_rate - avg_loss * loss_rate

    # Equity curve
    equity_curve = _build_equity_curve(trades, initial_bankroll)
    max_dd, max_dd_pct = _max_drawdown(equity_curve)

    # Sharpe & Sortino
    returns = _trade_returns(trades)
    sharpe = _sharpe_ratio(returns)
    sortino = _sortino_ratio(returns)

    # Recovery factor
    recovery_factor = total_pnl / max_dd if max_dd > 0 else float("inf")

    # Consecutive streaks
    max_con_wins, max_con_losses = _consecutive_streaks(trades)

    final_bankroll = equity_curve[-1] if equity_curve else initial_bankroll

    return PerformanceMetrics(
        total_trades=total_trades,
        wins=n_wins,
        losses=n_losses,
        win_rate=round(win_rate, 4),
        total_pnl=round(total_pnl, 4),
        avg_pnl_per_trade=round(avg_pnl, 4),
        gross_profit=round(gross_profit, 4),
        gross_loss=round(gross_loss, 4),
        profit_factor=round(profit_factor, 4) if profit_factor != float("inf") else 999.99,
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        max_drawdown=round(max_dd, 4),
        max_drawdown_pct=round(max_dd_pct, 4),
        recovery_factor=round(recovery_factor, 4) if recovery_factor != float("inf") else 999.99,
        avg_win=round(avg_win, 4),
        avg_loss=round(avg_loss, 4),
        max_consecutive_wins=max_con_wins,
        max_consecutive_losses=max_con_losses,
        expectancy=round(expectancy, 4),
        final_bankroll=round(final_bankroll, 4),
    )


def _build_equity_curve(
    trades: list[SimulatedTrade], initial_bankroll: float,
) -> list[float]:
    """Build equity curve from trade sequence."""
    curve = [initial_bankroll]
    equity = initial_bankroll
    for t in trades:
        equity += t.pnl
        curve.append(equity)
    return curve


def _max_drawdown(equity_curve: list[float]) -> tuple[float, float]:
    """Return (absolute max drawdown, percentage max drawdown)."""
    if len(equity_curve) < 2:
        return 0.0, 0.0

    arr = np.array(equity_curve)
    peak = np.maximum.accumulate(arr)
    drawdowns = peak - arr
    max_dd = float(np.max(drawdowns))

    # Percentage relative to peak at the time
    with np.errstate(divide="ignore", invalid="ignore"):
        dd_pct = np.where(peak > 0, drawdowns / peak, 0.0)
    max_dd_pct = float(np.max(dd_pct))

    return max_dd, max_dd_pct


def _trade_returns(trades: list[SimulatedTrade]) -> list[float]:
    """Per-trade percentage returns relative to bet cost."""
    returns = []
    for t in trades:
        cost = t.entry_price * t.bet_size
        if cost > 0:
            returns.append(t.pnl / cost)
        else:
            returns.append(0.0)
    return returns


def _sharpe_ratio(returns: list[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio.

    For high-frequency binary trades we use sqrt(N) annualization
    where N = trades per year (approx 365 * 24 * 4 for 15-min).
    Since trade frequency varies, we just report raw (per-trade) Sharpe.
    """
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess = arr - risk_free
    std = float(np.std(excess, ddof=1))
    if std == 0:
        return 0.0
    return float(np.mean(excess)) / std


def _sortino_ratio(returns: list[float], risk_free: float = 0.0) -> float:
    """Sortino ratio — penalizes downside volatility only."""
    if len(returns) < 2:
        return 0.0
    arr = np.array(returns)
    excess = arr - risk_free
    downside = excess[excess < 0]
    if len(downside) == 0:
        return float(np.mean(excess)) / 0.0001  # near-perfect
    downside_std = float(np.std(downside, ddof=1))
    if downside_std == 0:
        return 0.0
    return float(np.mean(excess)) / downside_std


def _consecutive_streaks(trades: list[SimulatedTrade]) -> tuple[int, int]:
    """Return (max consecutive wins, max consecutive losses)."""
    max_wins = max_losses = 0
    cur_wins = cur_losses = 0

    for t in trades:
        if t.exit_outcome == "WIN":
            cur_wins += 1
            cur_losses = 0
            max_wins = max(max_wins, cur_wins)
        else:
            cur_losses += 1
            cur_wins = 0
            max_losses = max(max_losses, cur_losses)

    return max_wins, max_losses


def build_equity_curve(
    trades: list[SimulatedTrade], initial_bankroll: float,
) -> list[float]:
    """Public wrapper for equity curve building."""
    return _build_equity_curve(trades, initial_bankroll)


def build_drawdown_curve(equity_curve: list[float]) -> list[float]:
    """Build drawdown curve from equity curve."""
    if not equity_curve:
        return []
    arr = np.array(equity_curve)
    peak = np.maximum.accumulate(arr)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd_pct = np.where(peak > 0, (peak - arr) / peak, 0.0)
    return [round(float(x), 6) for x in dd_pct]
