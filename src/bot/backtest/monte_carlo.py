"""Monte Carlo simulation for backtest robustness testing.

Validates whether a strategy's edge is robust to trade-order randomness
by shuffling the trade sequence many times and recomputing performance
statistics on each permutation.  Distributions of final equity, maximum
drawdown, and Sharpe ratio are reported at key percentiles, together
with the probability of profit and the probability of ruin.
"""
from __future__ import annotations

import logging
import random
from typing import Sequence

import numpy as np

from bot.backtest.models import (
    BacktestConfig,
    MonteCarloResult,
    SimulatedTrade,
)

logger = logging.getLogger(__name__)

_RUIN_THRESHOLD = 0.10   # bankroll below 10 % of initial = ruin


class MonteCarloAnalyzer:
    """Monte Carlo analysis via random permutation of trade order.

    Takes the trade list produced by a completed backtest, shuffles the
    sequence ``N`` times, and replays the P&L stream from the initial
    bankroll each time.  This answers the question: *"Is the strategy's
    edge order-independent, or did we just get lucky with the sequence?"*

    Example::

        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(backtest_result.trades, config)
        print(f"Prob profit: {result.prob_profit:.1%}")
        print(f"Prob ruin:   {result.prob_ruin:.1%}")
    """

    def run(
        self,
        trades: list[SimulatedTrade],
        config: BacktestConfig,
        seed: int | None = None,
    ) -> MonteCarloResult:
        """Run Monte Carlo simulation on a completed set of trades.

        For each of ``config.mc_iterations`` iterations the trade list is
        randomly shuffled and the equity curve is recomputed from
        ``config.initial_bankroll``.  Bet sizes are kept as-is; only the
        order changes.  Summary statistics are computed across all runs.

        Args:
            trades: Ordered list of ``SimulatedTrade`` from a completed
                backtest.  At least 2 trades are required; an empty or
                single-trade list returns a zeroed result.
            config: Backtest configuration.  Uses ``initial_bankroll``,
                ``mc_iterations``, and ``mc_confidence``.
            seed: Optional integer seed for the PRNG.  When provided the
                simulation is fully reproducible.  Defaults to ``None``
                (non-deterministic).

        Returns:
            A ``MonteCarloResult`` containing percentile distributions of
            final equity, max drawdown, Sharpe ratio, and the probabilities
            of profit and ruin.
        """
        n_trades = len(trades)

        if n_trades < 2:
            logger.warning(
                "MonteCarloAnalyzer: fewer than 2 trades — returning zeroed result"
            )
            return MonteCarloResult(
                iterations=0,
                confidence_level=config.mc_confidence,
            )

        rng = random.Random(seed)
        np_rng = np.random.default_rng(seed)

        n_iter = config.mc_iterations
        initial = config.initial_bankroll

        # Pre-extract the P&L values so inner loop only touches plain floats
        pnl_values: list[float] = [t.pnl for t in trades]

        # Per-trade returns for Sharpe calculation: pnl / cost
        # cost = entry_price * bet_size (mirrors metrics._trade_returns)
        costs: list[float] = [
            t.entry_price * t.bet_size if t.entry_price * t.bet_size > 0 else 1.0
            for t in trades
        ]
        base_returns: list[float] = [pnl / cost for pnl, cost in zip(pnl_values, costs)]

        final_equities: list[float] = []
        max_drawdowns: list[float] = []
        sharpe_ratios: list[float] = []

        logger.debug(
            "MonteCarloAnalyzer: running %d iterations on %d trades",
            n_iter,
            n_trades,
        )

        for _ in range(n_iter):
            # Shuffle trade indices (cheaper than copying objects)
            indices = list(range(n_trades))
            rng.shuffle(indices)

            # Build shuffled equity curve
            equity = initial
            peak = initial
            max_dd = 0.0
            shuffled_returns: list[float] = []

            for idx in indices:
                equity += pnl_values[idx]
                shuffled_returns.append(base_returns[idx])
                if equity > peak:
                    peak = equity
                dd = peak - equity
                if dd > max_dd:
                    max_dd = dd

            final_equities.append(equity)
            max_drawdowns.append(max_dd)
            sharpe_ratios.append(_sharpe(shuffled_returns))

        # Convert to numpy arrays for fast percentile computation
        eq_arr = np.array(final_equities, dtype=np.float64)
        dd_arr = np.array(max_drawdowns, dtype=np.float64)
        sr_arr = np.array(sharpe_ratios, dtype=np.float64)

        # Probabilities
        prob_profit = float(np.mean(eq_arr > initial))
        ruin_threshold = initial * _RUIN_THRESHOLD
        prob_ruin = float(np.mean(eq_arr < ruin_threshold))

        return MonteCarloResult(
            iterations=n_iter,
            confidence_level=config.mc_confidence,
            # Final equity distribution
            median_final_equity=round(float(np.percentile(eq_arr, 50)), 4),
            p5_final_equity=round(float(np.percentile(eq_arr, 5)), 4),
            p95_final_equity=round(float(np.percentile(eq_arr, 95)), 4),
            # Max drawdown distribution (higher = worse)
            median_max_drawdown=round(float(np.percentile(dd_arr, 50)), 4),
            p95_max_drawdown=round(float(np.percentile(dd_arr, 95)), 4),
            # Sharpe distribution
            median_sharpe=round(float(np.percentile(sr_arr, 50)), 4),
            p5_sharpe=round(float(np.percentile(sr_arr, 5)), 4),
            p95_sharpe=round(float(np.percentile(sr_arr, 95)), 4),
            # Summary probabilities
            prob_profit=round(prob_profit, 4),
            prob_ruin=round(prob_ruin, 4),
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _sharpe(returns: Sequence[float], risk_free: float = 0.0) -> float:
    """Per-trade Sharpe ratio for a single Monte Carlo run.

    Args:
        returns: Sequence of per-trade percentage returns.
        risk_free: Risk-free rate per trade (default 0.0).

    Returns:
        Sharpe ratio, or 0.0 when there are fewer than 2 observations or
        the standard deviation of returns is zero.
    """
    if len(returns) < 2:
        return 0.0
    arr = np.asarray(returns, dtype=np.float64)
    excess = arr - risk_free
    std = float(np.std(excess, ddof=1))
    if std == 0.0:
        return 0.0
    return float(np.mean(excess) / std)


def interpret_monte_carlo(result: MonteCarloResult, initial_bankroll: float) -> str:
    """Return a plain-text summary of Monte Carlo simulation results.

    Args:
        result: A completed ``MonteCarloResult``.
        initial_bankroll: The starting capital used in the simulation.

    Returns:
        Multi-line string with key statistics and a GO/NO-GO verdict.
    """
    lines: list[str] = [
        f"Monte Carlo — {result.iterations} iterations",
        f"  Prob profit : {result.prob_profit:.1%}",
        f"  Prob ruin   : {result.prob_ruin:.1%}",
        f"  Equity P5/med/P95 : "
        f"{result.p5_final_equity:.2f} / "
        f"{result.median_final_equity:.2f} / "
        f"{result.p95_final_equity:.2f}",
        f"  Max DD med/P95    : "
        f"{result.median_max_drawdown:.2f} / "
        f"{result.p95_max_drawdown:.2f}",
        f"  Sharpe P5/med/P95 : "
        f"{result.p5_sharpe:.3f} / "
        f"{result.median_sharpe:.3f} / "
        f"{result.p95_sharpe:.3f}",
    ]

    if result.prob_profit >= 0.65 and result.prob_ruin <= 0.05:
        verdict = "GO — edge is robust to trade-order randomness"
    elif result.prob_ruin > 0.20:
        verdict = "NO-GO — ruin probability too high"
    elif result.prob_profit < 0.50:
        verdict = "NO-GO — majority of permutations end below breakeven"
    else:
        verdict = "MARGINAL — review drawdown and Sharpe distributions"

    lines.append(f"  Verdict     : {verdict}")
    return "\n".join(lines)
