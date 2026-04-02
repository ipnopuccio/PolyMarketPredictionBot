"""Walk-forward analysis for backtest validation.

Splits the full date range into rolling train/test windows and measures
how well in-sample performance translates to out-of-sample performance.
A high overfitting_score (> 2.0) signals that parameter tuning has
memorised the training data.
"""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import TYPE_CHECKING

from bot.backtest.metrics import compute_metrics
from bot.backtest.models import (
    BacktestConfig,
    PerformanceMetrics,
    SimulatedTrade,
    WalkForwardResult,
    WalkForwardWindow,
)

if TYPE_CHECKING:
    from bot.backtest.engine import BacktestEngine

logger = logging.getLogger(__name__)

_DEFAULT_NUM_WINDOWS = 5
_RUIN_SHARPE = 0.0001  # sentinel when a window produces no trades


class WalkForwardAnalyzer:
    """Rolling walk-forward analysis over a backtest date range.

    Each window is divided into a training period (in-sample) and a
    test period (out-of-sample) according to ``config.train_ratio``.
    The analyzer runs the backtest engine on both halves and stores the
    resulting metrics.  All out-of-sample trades are then aggregated to
    produce a single combined OOS performance view.

    Example::

        analyzer = WalkForwardAnalyzer(num_windows=5)
        result = await analyzer.run(config, engine)
        print(result.overfitting_score)   # < 2.0 is healthy
    """

    def __init__(self, num_windows: int = _DEFAULT_NUM_WINDOWS) -> None:
        """Initialise the analyzer.

        Args:
            num_windows: Number of rolling windows to create.  Each window
                covers (total_days / num_windows) days.  Defaults to 5.
        """
        if num_windows < 2:
            raise ValueError("num_windows must be at least 2")
        self.num_windows = num_windows

    async def run(
        self,
        config: BacktestConfig,
        engine: "BacktestEngine",
    ) -> WalkForwardResult:
        """Execute walk-forward analysis over the config date range.

        Splits the date range into ``num_windows`` non-overlapping windows.
        For each window the engine is run twice — once on the training
        slice (in-sample) and once on the test slice (out-of-sample).

        Args:
            config: Backtest configuration including ``train_ratio``,
                ``start_date``, and ``end_date``.
            engine: A ``BacktestEngine`` instance whose ``run()`` method
                accepts a ``BacktestConfig`` and returns a
                ``BacktestResult``.

        Returns:
            A ``WalkForwardResult`` containing per-window metrics,
            aggregated out-of-sample metrics, and an overfitting score.
        """
        total_seconds = (config.end_date - config.start_date).total_seconds()
        window_seconds = total_seconds / self.num_windows

        windows: list[WalkForwardWindow] = []
        all_oos_trades: list[SimulatedTrade] = []

        for idx in range(self.num_windows):
            window_start = config.start_date.replace(microsecond=0) + \
                _seconds_delta(idx * window_seconds)
            window_end = config.start_date.replace(microsecond=0) + \
                _seconds_delta((idx + 1) * window_seconds)

            # Clamp the last window to the exact config end date
            if idx == self.num_windows - 1:
                window_end = config.end_date

            split_seconds = (window_end - window_start).total_seconds() * config.train_ratio
            train_end = window_start + _seconds_delta(split_seconds)
            test_start = train_end

            logger.debug(
                "Walk-forward window %d/%d  train=[%s, %s]  test=[%s, %s]",
                idx + 1,
                self.num_windows,
                window_start.date(),
                train_end.date(),
                test_start.date(),
                window_end.date(),
            )

            # --- In-sample run ---
            train_config = _slice_config(config, window_start, train_end)
            train_result = await engine.run(train_config)
            in_sample_metrics = train_result.metrics

            # --- Out-of-sample run ---
            test_config = _slice_config(config, test_start, window_end)
            test_result = await engine.run(test_config)
            oos_metrics = test_result.metrics

            windows.append(
                WalkForwardWindow(
                    window_index=idx,
                    train_start=window_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=window_end,
                    in_sample=in_sample_metrics,
                    out_of_sample=oos_metrics,
                )
            )

            all_oos_trades.extend(test_result.trades)

        aggregated_oos = compute_metrics(all_oos_trades, config.initial_bankroll)
        overfitting_score = _compute_overfitting_score(windows)

        return WalkForwardResult(
            config=config,
            windows=windows,
            aggregated_oos=aggregated_oos,
            overfitting_score=round(overfitting_score, 4),
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _seconds_delta(seconds: float):
    """Return a timedelta for a fractional number of seconds."""
    from datetime import timedelta
    return timedelta(seconds=int(seconds))


def _slice_config(
    base: BacktestConfig,
    start,
    end,
) -> BacktestConfig:
    """Return a copy of *base* with start_date and end_date replaced."""
    data = base.model_dump()
    data["start_date"] = start
    data["end_date"] = end
    return BacktestConfig(**data)


def _compute_overfitting_score(windows: list[WalkForwardWindow]) -> float:
    """Compute the average IS/OOS Sharpe ratio across all windows.

    The score is defined as the mean of (in_sample_sharpe / oos_sharpe)
    for each window.  Values above 2.0 suggest the strategy has been
    over-fitted to the training data.

    A small sentinel value (``_RUIN_SHARPE``) is substituted whenever a
    window's OOS Sharpe is zero, preventing division-by-zero while still
    producing a visibly elevated score that flags the problem.

    Args:
        windows: List of completed walk-forward windows.

    Returns:
        Mean overfitting score across all windows (clamped to 999.99 max).
    """
    if not windows:
        return 0.0

    ratios: list[float] = []
    for w in windows:
        is_sharpe = abs(w.in_sample.sharpe_ratio)
        oos_sharpe = abs(w.out_of_sample.sharpe_ratio)

        if oos_sharpe == 0.0:
            # Guard: either no OOS trades or flat equity — use sentinel
            oos_sharpe = _RUIN_SHARPE

        ratio = is_sharpe / oos_sharpe
        ratios.append(ratio)

    mean_ratio = sum(ratios) / len(ratios)
    return min(mean_ratio, 999.99)


def interpret_overfitting_score(score: float) -> str:
    """Return a human-readable verdict for an overfitting score.

    Args:
        score: The ``overfitting_score`` from a ``WalkForwardResult``.

    Returns:
        A plain-text verdict string.
    """
    if score < 1.5:
        return "ROBUST — IS and OOS performance are closely aligned"
    if score < 2.0:
        return "ACCEPTABLE — minor degradation from IS to OOS"
    if score < 3.0:
        return "WARNING — strategy may be overfit; review parameters"
    return "OVERFIT — IS Sharpe far exceeds OOS; do not trade live"
