"""Pydantic models for backtest configuration and results."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class BacktestConfig(BaseModel):
    """Input parameters for a backtest run."""
    strategy: str
    asset: str
    start_date: datetime
    end_date: datetime
    initial_bankroll: float = 40.0
    slippage_bps: float = 50.0       # basis points (0.5%)
    commission_pct: float = 0.02     # 2% taker fee on winnings
    gas_per_trade: float = 0.01      # USDC gas cost
    kelly_fraction: float = 1 / 3
    use_kelly: bool = True
    # Walk-forward specific
    train_ratio: float = 0.7         # 70% train, 30% test
    # Monte Carlo specific
    mc_iterations: int = 1000
    mc_confidence: float = 0.95


class SimulatedTrade(BaseModel):
    """A single simulated trade in the backtest."""
    timestamp: datetime
    signal: Literal["BUY_YES", "BUY_NO"]
    entry_price: float
    exit_outcome: Literal["WIN", "LOSS"]
    bet_size: float
    pnl: float                       # net P&L after slippage + commission
    confidence: float
    bankroll_after: float
    indicators: dict[str, float | None] = Field(default_factory=dict)


class PerformanceMetrics(BaseModel):
    """Comprehensive performance metrics for a backtest."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    recovery_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    expectancy: float = 0.0          # avg win * win_rate - avg loss * loss_rate
    final_bankroll: float = 0.0


class BacktestResult(BaseModel):
    """Complete result of a single backtest run."""
    config: BacktestConfig
    metrics: PerformanceMetrics
    trades: list[SimulatedTrade] = Field(default_factory=list)
    equity_curve: list[float] = Field(default_factory=list)
    drawdown_curve: list[float] = Field(default_factory=list)
    timestamps: list[datetime] = Field(default_factory=list)
    run_duration_ms: float = 0.0


class WalkForwardWindow(BaseModel):
    """Result of a single walk-forward window."""
    window_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    in_sample: PerformanceMetrics
    out_of_sample: PerformanceMetrics


class WalkForwardResult(BaseModel):
    """Aggregated walk-forward results."""
    config: BacktestConfig
    windows: list[WalkForwardWindow] = Field(default_factory=list)
    aggregated_oos: PerformanceMetrics = Field(default_factory=PerformanceMetrics)
    overfitting_score: float = 0.0   # ratio of IS vs OOS Sharpe


class MonteCarloResult(BaseModel):
    """Monte Carlo simulation results."""
    iterations: int
    confidence_level: float
    # Distribution of final equity
    median_final_equity: float = 0.0
    p5_final_equity: float = 0.0     # 5th percentile (worst case)
    p95_final_equity: float = 0.0    # 95th percentile (best case)
    # Distribution of max drawdown
    median_max_drawdown: float = 0.0
    p95_max_drawdown: float = 0.0    # 95th percentile worst drawdown
    # Distribution of Sharpe
    median_sharpe: float = 0.0
    p5_sharpe: float = 0.0
    p95_sharpe: float = 0.0
    # Probability of profit
    prob_profit: float = 0.0
    # Probability of ruin (bankroll < 10% of initial)
    prob_ruin: float = 0.0


class FullBacktestReport(BaseModel):
    """Complete backtest report including all analyses."""
    backtest: BacktestResult
    walk_forward: WalkForwardResult | None = None
    monte_carlo: MonteCarloResult | None = None
