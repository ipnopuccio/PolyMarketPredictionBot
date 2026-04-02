"""Core backtest engine for strategy replay and simulation.

Supports two execution modes:
  - Synthetic: generate FeedSnapshots via data_provider and replay the
    actual strategy classes against them.
  - DB replay: load resolved historical trades from SQLite and re-simulate
    P&L under the configured fee/slippage model.

All monetary values are in USDC. Binary market semantics:
  WIN  → payout of 1.0 per share; profit = (1 - entry_price) * bet_shares
  LOSS → full loss of entry cost; profit = -(entry_price * bet_shares)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Literal

from bot.backtest.data_provider import (
    generate_bollinger_data,
    generate_rsi,
    generate_synthetic_snapshots,
    load_trades_from_db,
)
from bot.backtest.metrics import (
    build_drawdown_curve,
    build_equity_curve,
    compute_metrics,
)
from bot.backtest.models import (
    BacktestConfig,
    BacktestResult,
    SimulatedTrade,
)
from bot.config import (
    BollingerConfig,
    MomentumConfig,
    TurboCvdConfig,
    TurboVwapConfig,
    settings,
)
from bot.core.types import FeedSnapshot, Signal, SignalResult
from bot.strategies.base import BaseStrategy
from bot.strategies.bollinger import BollingerStrategy
from bot.strategies.momentum import MomentumStrategy
from bot.strategies.turbo_cvd import TurboCvdStrategy
from bot.strategies.turbo_vwap import TurboVwapStrategy

logger = logging.getLogger(__name__)

# Number of data points to generate when no explicit count is given.
_DEFAULT_SYNTHETIC_POINTS = 1_000

# Rolling window length used to estimate win-rate for Kelly sizing.
_KELLY_WARMUP = 20

# Minimum bankroll fraction; below this we skip trades to avoid ruin.
_MIN_BANKROLL_FRACTION = 0.01


def _build_strategy(strategy_name: str) -> BaseStrategy:
    """Instantiate the strategy class for the given name.

    Args:
        strategy_name: One of MOMENTUM, BOLLINGER, TURBO_CVD, TURBO_VWAP.

    Returns:
        Configured strategy instance.

    Raises:
        ValueError: If the strategy name is not recognised.
    """
    cfg_map = settings.strategy_configs
    name = strategy_name.upper()

    if name == "MOMENTUM":
        cfg = cfg_map.get("MOMENTUM", MomentumConfig())
        return MomentumStrategy(cfg)  # type: ignore[arg-type]
    if name == "BOLLINGER":
        cfg = cfg_map.get("BOLLINGER", BollingerConfig())
        return BollingerStrategy(cfg)  # type: ignore[arg-type]
    if name == "TURBO_CVD":
        cfg = cfg_map.get("TURBO_CVD", TurboCvdConfig())
        return TurboCvdStrategy(cfg)  # type: ignore[arg-type]
    if name == "TURBO_VWAP":
        cfg = cfg_map.get("TURBO_VWAP", TurboVwapConfig())
        return TurboVwapStrategy(cfg)  # type: ignore[arg-type]

    raise ValueError(
        f"Unknown strategy '{strategy_name}'. "
        "Valid choices: MOMENTUM, BOLLINGER, TURBO_CVD, TURBO_VWAP"
    )


def _apply_slippage(price: float, slippage_bps: float) -> float:
    """Add slippage to an entry price.

    For binary options a higher entry price is always worse for the buyer,
    so slippage uniformly increases the effective cost.

    Args:
        price: Raw entry price (0.0–1.0).
        slippage_bps: Slippage in basis points (e.g. 50 = 0.50%).

    Returns:
        Adjusted entry price, clamped to [0.001, 0.999].
    """
    adjusted = price * (1.0 + slippage_bps / 10_000.0)
    return max(0.001, min(0.999, adjusted))


def _kelly_bet_size(
    bankroll: float,
    win_rate: float,
    entry_price: float,
    kelly_fraction: float,
    config: BacktestConfig,
) -> float:
    """Compute fractional Kelly bet size in USDC.

    Formula:  f* = win_rate - (1 - win_rate) * (p / (1 - p))
    where p is the entry price (cost per share).

    The result is then multiplied by ``kelly_fraction`` for safety and
    clamped between 1% and 10% of current bankroll.

    Args:
        bankroll: Current bankroll in USDC.
        win_rate: Historical win-rate estimate (0.0–1.0).
        entry_price: Cost per share after slippage (0.001–0.999).
        kelly_fraction: Fractional Kelly multiplier (e.g. 1/3).
        config: BacktestConfig carrying initial_bankroll for floor calc.

    Returns:
        Bet size in USDC, always positive and safe-floored.
    """
    if entry_price >= 1.0:
        return bankroll * 0.01

    full_kelly = win_rate - (1.0 - win_rate) * (entry_price / (1.0 - entry_price))
    frac_kelly = full_kelly * kelly_fraction

    if frac_kelly <= 0.0:
        return bankroll * 0.01

    # Cap between 1% and 10% of current bankroll
    min_bet = max(bankroll * 0.01, 0.10)
    max_bet = bankroll * 0.10
    raw = bankroll * frac_kelly
    return round(max(min_bet, min(max_bet, raw)), 4)


def _fixed_bet_size(bankroll: float, config: BacktestConfig) -> float:
    """Flat 3% of bankroll when Kelly is disabled.

    Args:
        bankroll: Current bankroll in USDC.
        config: BacktestConfig (used for floor reference).

    Returns:
        Fixed bet size in USDC.
    """
    return round(max(bankroll * 0.03, 0.10), 4)


def _determine_outcome(
    signal: Signal,
    current_price: float,
    next_price: float,
) -> Literal["WIN", "LOSS"]:
    """Determine whether a binary trade wins or loses.

    A BUY_YES (price going up) wins if the next observed price is higher.
    A BUY_NO (price going down) wins if the next observed price is lower.

    Args:
        signal: BUY_YES or BUY_NO.
        current_price: Underlying asset price at signal time.
        next_price: Underlying asset price at the next snapshot.

    Returns:
        "WIN" or "LOSS".
    """
    if signal == Signal.BUY_YES:
        return "WIN" if next_price > current_price else "LOSS"
    # BUY_NO
    return "WIN" if next_price < current_price else "LOSS"


def _compute_pnl(
    outcome: Literal["WIN", "LOSS"],
    entry_price: float,
    bet_size: float,
    commission_pct: float,
    gas_per_trade: float,
) -> float:
    """Compute net P&L for a single binary trade.

    The binary payout model mirrors Polymarket:
      - Each share costs ``entry_price`` USDC.
      - A WIN pays 1.0 USDC per share; gross profit = (1 - entry_price) * shares.
      - A LOSS returns 0; gross loss = entry_price * shares.
      - ``commission_pct`` is charged on gross winnings only.
      - ``gas_per_trade`` is always deducted.

    Args:
        outcome: "WIN" or "LOSS".
        entry_price: Effective entry price per share after slippage.
        bet_size: Total USDC wagered (= shares * entry_price).
        commission_pct: Commission rate applied to gross profit on wins.
        gas_per_trade: Fixed gas cost per trade in USDC.

    Returns:
        Net P&L in USDC (positive = profit, negative = loss).
    """
    shares = bet_size / entry_price
    if outcome == "WIN":
        gross_profit = (1.0 - entry_price) * shares
        net_profit = gross_profit * (1.0 - commission_pct)
        return round(net_profit - gas_per_trade, 6)
    else:
        return round(-(bet_size + gas_per_trade), 6)


class BacktestEngine:
    """Replay engine for strategy backtesting on binary Polymarket markets.

    Example usage::

        config = BacktestConfig(
            strategy="MOMENTUM",
            asset="BTC",
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 3, 1),
        )
        engine = BacktestEngine()
        result = await engine.run(config)
    """

    def __init__(self) -> None:
        pass

    async def run(
        self,
        config: BacktestConfig,
        num_points: int = _DEFAULT_SYNTHETIC_POINTS,
        seed: int | None = 42,
    ) -> BacktestResult:
        """Run a full synthetic backtest.

        Generates ``num_points`` FeedSnapshots via GBM, replays the real
        strategy class against each snapshot, and simulates trade outcomes
        based on whether the underlying price moved in the signalled direction.

        Args:
            config: Backtest parameters (strategy, asset, dates, fees, etc.).
            num_points: Number of synthetic data points to generate.
            seed: Random seed for reproducibility. Pass None for random runs.

        Returns:
            BacktestResult containing all trades, equity/drawdown curves,
            and computed performance metrics.
        """
        t0 = time.perf_counter()

        strategy = _build_strategy(config.strategy)
        snapshots = generate_synthetic_snapshots(
            config,
            num_points=num_points,
            seed=seed,
        )

        # Pre-compute RSI and Bollinger for the entire price series so that
        # strategies that need them (BOLLINGER) receive valid data.
        prices = [snap.last_price for _, snap in snapshots]
        rsi_series = generate_rsi(prices, period=14)

        bb_period = 20
        bb_std = 1.5
        if config.strategy == "BOLLINGER":
            bollinger_cfg = settings.strategy_configs.get("BOLLINGER")
            if isinstance(bollinger_cfg, BollingerConfig):
                bb_period = bollinger_cfg.bb_period
                bb_std = bollinger_cfg.bb_std
        bb_series = generate_bollinger_data(prices, period=bb_period, std_mult=bb_std)

        trades = self._replay_snapshots(
            config=config,
            strategy=strategy,
            snapshots=snapshots,
            rsi_series=rsi_series,
            bb_series=bb_series,
        )

        return self._build_result(config, trades, t0)

    async def run_from_db(
        self,
        config: BacktestConfig,
        db,  # bot.storage.database.Database — avoid circular import
    ) -> BacktestResult:
        """Re-simulate P&L from historical resolved trades in the database.

        Loads real trade records (with known outcomes) and applies the fee
        model from ``config`` to produce a comparable BacktestResult. The
        outcome (WIN/LOSS) is taken directly from the database — this method
        does not re-evaluate strategy logic, it only re-applies cost structure.

        Args:
            config: Backtest parameters (fees, slippage, Kelly settings).
            db: An open Database instance to query historical trades.

        Returns:
            BacktestResult built from real historical outcomes.
        """
        t0 = time.perf_counter()

        raw_trades = await load_trades_from_db(
            db=db,
            strategy=config.strategy,
            asset=config.asset,
            start_date=config.start_date,
            end_date=config.end_date,
        )

        if not raw_trades:
            logger.warning(
                "run_from_db: no resolved trades found for %s/%s in [%s, %s]",
                config.strategy,
                config.asset,
                config.start_date.isoformat(),
                config.end_date.isoformat(),
            )
            return self._build_result(config, [], t0)

        trades = self._simulate_from_db_records(config, raw_trades)
        return self._build_result(config, trades, t0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _replay_snapshots(
        self,
        config: BacktestConfig,
        strategy: BaseStrategy,
        snapshots: list[tuple[datetime, FeedSnapshot]],
        rsi_series: list[float | None],
        bb_series: list[dict | None],
    ) -> list[SimulatedTrade]:
        """Core replay loop: evaluate strategy on each snapshot.

        Maintains a rolling win-rate window for Kelly sizing and simulates
        outcome using next-bar price movement.

        Args:
            config: BacktestConfig providing fee and sizing parameters.
            strategy: Instantiated strategy to evaluate.
            snapshots: Time-ordered (timestamp, FeedSnapshot) pairs.
            rsi_series: RSI values aligned with snapshots (None during warmup).
            bb_series: Bollinger Band dicts aligned with snapshots (None during warmup).

        Returns:
            Ordered list of SimulatedTrade records.
        """
        trades: list[SimulatedTrade] = []
        bankroll = config.initial_bankroll

        # Rolling window for Kelly win-rate estimation
        recent_outcomes: list[bool] = []  # True = WIN

        for i, (ts, snap) in enumerate(snapshots):
            # We need a "next" price to determine the outcome; skip the last bar.
            if i >= len(snapshots) - 1:
                break

            rsi = rsi_series[i]
            bb = bb_series[i]

            result: SignalResult = strategy.evaluate(
                asset=config.asset,
                snapshot=snap,
                rsi=rsi,
                bb=bb,
            )

            if result.signal == Signal.SKIP:
                continue

            # Skip if bankroll is critically low
            if bankroll < config.initial_bankroll * _MIN_BANKROLL_FRACTION:
                logger.debug("Bankroll critically low (%.4f), skipping trade.", bankroll)
                continue

            # Entry price: use the Polymarket ask proxy.
            # For BUY_YES we use ask; for BUY_NO we also use ask (price of the
            # opposing token is symmetric in a binary market).
            raw_entry = snap.ask if snap.ask > 0.0 else 0.50
            # Normalise to binary probability domain [0.001, 0.999]
            if raw_entry > 1.0:
                # Ask was an asset price, not a probability — use fixed estimate
                raw_entry = 0.50

            entry_price = _apply_slippage(raw_entry, config.slippage_bps)

            # Validate entry guard on the strategy
            if not strategy.entry_ok(config.asset, result.signal, entry_price):
                continue

            # Bet sizing
            if config.use_kelly and len(recent_outcomes) >= _KELLY_WARMUP:
                estimated_wr = sum(recent_outcomes) / len(recent_outcomes)
                bet_size = _kelly_bet_size(
                    bankroll=bankroll,
                    win_rate=estimated_wr,
                    entry_price=entry_price,
                    kelly_fraction=config.kelly_fraction,
                    config=config,
                )
            else:
                bet_size = _fixed_bet_size(bankroll, config)

            # Clamp bet to available bankroll
            bet_size = min(bet_size, bankroll)

            # Determine outcome from next bar's price
            next_price = snapshots[i + 1][1].last_price
            outcome = _determine_outcome(result.signal, snap.last_price, next_price)

            pnl = _compute_pnl(
                outcome=outcome,
                entry_price=entry_price,
                bet_size=bet_size,
                commission_pct=config.commission_pct,
                gas_per_trade=config.gas_per_trade,
            )

            bankroll = round(bankroll + pnl, 6)

            # Keep the rolling win-rate window at most 50 entries
            recent_outcomes.append(outcome == "WIN")
            if len(recent_outcomes) > 50:
                recent_outcomes.pop(0)

            trade = SimulatedTrade(
                timestamp=ts,
                signal=result.signal.value,  # type: ignore[arg-type]
                entry_price=round(entry_price, 6),
                exit_outcome=outcome,
                bet_size=round(bet_size, 6),
                pnl=pnl,
                confidence=round(result.confidence, 4),
                bankroll_after=round(bankroll, 4),
                indicators={
                    k: (round(v, 6) if v is not None else None)
                    for k, v in result.indicators.items()
                },
            )
            trades.append(trade)

        return trades

    def _simulate_from_db_records(
        self,
        config: BacktestConfig,
        raw_trades: list[dict],
    ) -> list[SimulatedTrade]:
        """Apply fee model to resolved database trades.

        Outcomes are taken as-is from the database; only the P&L is
        recomputed using the config's fee parameters.

        Args:
            config: BacktestConfig with fee/slippage parameters to apply.
            raw_trades: Chronologically ordered resolved trade dicts from DB.

        Returns:
            List of SimulatedTrade with recomputed P&L.
        """
        trades: list[SimulatedTrade] = []
        bankroll = config.initial_bankroll
        recent_outcomes: list[bool] = []

        for rec in raw_trades:
            raw_entry = float(rec.get("entry_price", 0.50))
            entry_price = _apply_slippage(raw_entry, config.slippage_bps)

            if config.use_kelly and len(recent_outcomes) >= _KELLY_WARMUP:
                estimated_wr = sum(recent_outcomes) / len(recent_outcomes)
                bet_size = _kelly_bet_size(
                    bankroll=bankroll,
                    win_rate=estimated_wr,
                    entry_price=entry_price,
                    kelly_fraction=config.kelly_fraction,
                    config=config,
                )
            else:
                bet_size = _fixed_bet_size(bankroll, config)

            bet_size = min(bet_size, bankroll)

            outcome: Literal["WIN", "LOSS"] = (
                "WIN" if str(rec.get("outcome", "")).upper() == "WIN" else "LOSS"
            )

            pnl = _compute_pnl(
                outcome=outcome,
                entry_price=entry_price,
                bet_size=bet_size,
                commission_pct=config.commission_pct,
                gas_per_trade=config.gas_per_trade,
            )

            bankroll = round(bankroll + pnl, 6)

            recent_outcomes.append(outcome == "WIN")
            if len(recent_outcomes) > 50:
                recent_outcomes.pop(0)

            # Parse timestamp permissively
            ts_raw = rec.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(str(ts_raw))
            except (ValueError, TypeError):
                ts = datetime.utcnow()

            signal_str = str(rec.get("signal", "BUY_YES")).upper()
            if signal_str not in ("BUY_YES", "BUY_NO"):
                signal_str = "BUY_YES"

            trade = SimulatedTrade(
                timestamp=ts,
                signal=signal_str,  # type: ignore[arg-type]
                entry_price=round(entry_price, 6),
                exit_outcome=outcome,
                bet_size=round(bet_size, 6),
                pnl=pnl,
                confidence=float(rec.get("confidence", 0.0)),
                bankroll_after=round(bankroll, 4),
                indicators={
                    "cvd": rec.get("cvd_at_signal"),
                    "vwap_change": rec.get("vwap_change_at_signal"),
                    "rsi": rec.get("rsi_at_signal"),
                    "bb_pct": rec.get("bb_pct_at_signal"),
                    "funding": rec.get("funding_at_signal"),
                    "liq_long": rec.get("liq_long_at_signal"),
                    "liq_short": rec.get("liq_short_at_signal"),
                },
            )
            trades.append(trade)

        return trades

    def _build_result(
        self,
        config: BacktestConfig,
        trades: list[SimulatedTrade],
        t0: float,
    ) -> BacktestResult:
        """Assemble a BacktestResult from a completed trade list.

        Args:
            config: The original BacktestConfig.
            trades: All simulated trades in chronological order.
            t0: Timestamp from time.perf_counter() at run start.

        Returns:
            Fully populated BacktestResult.
        """
        metrics = compute_metrics(trades, config.initial_bankroll)
        equity_curve = build_equity_curve(trades, config.initial_bankroll)
        drawdown_curve = build_drawdown_curve(equity_curve)
        timestamps = [t.timestamp for t in trades]

        run_duration_ms = (time.perf_counter() - t0) * 1_000.0

        logger.info(
            "Backtest complete: strategy=%s asset=%s trades=%d win_rate=%.2f%% "
            "total_pnl=%.4f duration_ms=%.1f",
            config.strategy,
            config.asset,
            metrics.total_trades,
            metrics.win_rate * 100,
            metrics.total_pnl,
            run_duration_ms,
        )

        return BacktestResult(
            config=config,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            timestamps=timestamps,
            run_duration_ms=round(run_duration_ms, 3),
        )
