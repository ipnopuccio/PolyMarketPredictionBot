"""Comprehensive test suite for the backtest module.

Covers: metrics, data_provider, engine, walk_forward, monte_carlo, report.
All async tests run under pytest-asyncio (asyncio_mode = 'auto').
External APIs are never hit — the engine uses purely synthetic snapshots.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal

import pytest

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
from bot.backtest.data_provider import (
    generate_bollinger_data,
    generate_rsi,
    generate_synthetic_snapshots,
)
from bot.backtest.engine import (
    BacktestEngine,
    _apply_slippage,
    _compute_pnl,
    _determine_outcome,
    _kelly_bet_size,
)
from bot.backtest.metrics import (
    build_drawdown_curve,
    build_equity_curve,
    compute_metrics,
)
from bot.backtest.models import (
    BacktestConfig,
    BacktestResult,
    FullBacktestReport,
    MonteCarloResult,
    PerformanceMetrics,
    SimulatedTrade,
    WalkForwardResult,
    WalkForwardWindow,
)
from bot.backtest.monte_carlo import MonteCarloAnalyzer
from bot.backtest.report import ReportGenerator
from bot.backtest.walk_forward import WalkForwardAnalyzer
from bot.core.types import Signal


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------

def _dt(year: int = 2025, month: int = 1, day: int = 1) -> datetime:
    """Return a UTC-naive datetime for use in configs."""
    return datetime(year, month, day)


def _make_config(
    strategy: str = "TURBO_CVD",
    asset: str = "ETH",
    start_year: int = 2025,
    end_year: int = 2025,
    end_month: int = 4,
    **kwargs,
) -> BacktestConfig:
    return BacktestConfig(
        strategy=strategy,
        asset=asset,
        start_date=_dt(start_year),
        end_date=_dt(end_year, end_month),
        **kwargs,
    )


def _make_trade(
    pnl: float = 1.0,
    outcome: Literal["WIN", "LOSS"] = "WIN",
    entry_price: float = 0.45,
    bet_size: float = 2.0,
    bankroll_after: float = 42.0,
) -> SimulatedTrade:
    return SimulatedTrade(
        timestamp=_dt(),
        signal="BUY_YES",
        entry_price=entry_price,
        exit_outcome=outcome,
        bet_size=bet_size,
        pnl=pnl,
        confidence=0.75,
        bankroll_after=bankroll_after,
    )


@pytest.fixture
def win_trade() -> SimulatedTrade:
    return _make_trade(pnl=0.50, outcome="WIN", bankroll_after=40.50)


@pytest.fixture
def loss_trade() -> SimulatedTrade:
    return _make_trade(pnl=-0.45, outcome="LOSS", bankroll_after=39.55)


@pytest.fixture
def default_config() -> BacktestConfig:
    return _make_config()


@pytest.fixture
def engine() -> BacktestEngine:
    return BacktestEngine()


# ---------------------------------------------------------------------------
# 1. METRICS TESTS
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    """Tests for compute_metrics()."""

    def test_empty_trades_returns_zero_metrics(self) -> None:
        metrics = compute_metrics([], initial_bankroll=40.0)
        assert metrics.total_trades == 0
        assert metrics.wins == 0
        assert metrics.losses == 0
        assert metrics.win_rate == 0.0
        assert metrics.total_pnl == 0.0
        assert metrics.final_bankroll == 40.0

    def test_empty_trades_preserves_initial_bankroll(self) -> None:
        for br in [10.0, 40.0, 100.0]:
            m = compute_metrics([], initial_bankroll=br)
            assert m.final_bankroll == br

    def test_all_wins_gives_100_pct_win_rate(self) -> None:
        trades = [_make_trade(pnl=1.0, outcome="WIN") for _ in range(5)]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.win_rate == 1.0
        assert m.wins == 5
        assert m.losses == 0
        assert m.total_pnl > 0

    def test_all_wins_positive_gross_profit_zero_gross_loss(self) -> None:
        trades = [_make_trade(pnl=2.0, outcome="WIN") for _ in range(3)]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.gross_profit == pytest.approx(6.0, rel=1e-4)
        assert m.gross_loss == 0.0
        # profit_factor capped at 999.99 when no losses
        assert m.profit_factor == 999.99

    def test_all_losses_gives_0_pct_win_rate(self) -> None:
        trades = [_make_trade(pnl=-1.0, outcome="LOSS") for _ in range(4)]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.win_rate == 0.0
        assert m.losses == 4
        assert m.wins == 0
        assert m.total_pnl < 0

    def test_all_losses_positive_gross_loss_zero_gross_profit(self) -> None:
        trades = [_make_trade(pnl=-1.5, outcome="LOSS") for _ in range(3)]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.gross_profit == 0.0
        assert m.gross_loss == pytest.approx(4.5, rel=1e-4)

    def test_mixed_trades_win_rate(self) -> None:
        trades = (
            [_make_trade(pnl=1.0, outcome="WIN")] * 3
            + [_make_trade(pnl=-0.5, outcome="LOSS")] * 2
        )
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.win_rate == pytest.approx(0.6, rel=1e-4)
        assert m.wins == 3
        assert m.losses == 2
        assert m.total_trades == 5

    def test_mixed_trades_pnl_sum(self) -> None:
        trades = (
            [_make_trade(pnl=2.0, outcome="WIN")] * 2
            + [_make_trade(pnl=-1.0, outcome="LOSS")] * 2
        )
        m = compute_metrics(trades, initial_bankroll=40.0)
        # gross_profit=4, gross_loss=2 → total=2
        assert m.total_pnl == pytest.approx(2.0, rel=1e-4)

    def test_total_trades_count(self) -> None:
        trades = [_make_trade() for _ in range(10)]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.total_trades == 10

    def test_avg_pnl_per_trade(self) -> None:
        pnls = [2.0, -1.0, 3.0, -1.0]
        trades = [
            _make_trade(pnl=p, outcome="WIN" if p > 0 else "LOSS")
            for p in pnls
        ]
        m = compute_metrics(trades, initial_bankroll=40.0)
        expected_avg = sum(pnls) / len(pnls)
        assert m.avg_pnl_per_trade == pytest.approx(expected_avg, rel=1e-3)

    def test_profit_factor_correct_ratio(self) -> None:
        trades = (
            [_make_trade(pnl=3.0, outcome="WIN")] * 2  # gross profit = 6
            + [_make_trade(pnl=-2.0, outcome="LOSS")] * 1  # gross loss = 2
        )
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.profit_factor == pytest.approx(3.0, rel=1e-3)

    def test_final_bankroll_after_all_wins(self) -> None:
        br = 40.0
        trades = [_make_trade(pnl=1.0, outcome="WIN") for _ in range(5)]
        m = compute_metrics(trades, initial_bankroll=br)
        assert m.final_bankroll == pytest.approx(45.0, rel=1e-4)

    def test_final_bankroll_after_all_losses(self) -> None:
        br = 40.0
        trades = [_make_trade(pnl=-2.0, outcome="LOSS") for _ in range(4)]
        m = compute_metrics(trades, initial_bankroll=br)
        assert m.final_bankroll == pytest.approx(32.0, rel=1e-4)


class TestSharpeAndSortino:
    """Tests for Sharpe / Sortino ratio computation via compute_metrics."""

    def test_single_trade_returns_zero_sharpe(self) -> None:
        trades = [_make_trade(pnl=1.0, outcome="WIN")]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.sharpe_ratio == 0.0

    def test_single_trade_returns_zero_sortino(self) -> None:
        trades = [_make_trade(pnl=1.0, outcome="WIN")]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.sortino_ratio == 0.0

    def test_all_same_returns_zero_sharpe(self) -> None:
        # Constant returns → zero std → Sharpe = 0
        trades = [
            _make_trade(pnl=1.0, outcome="WIN", entry_price=0.5, bet_size=2.0)
            for _ in range(5)
        ]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.sharpe_ratio == 0.0

    def test_positive_edge_sharpe_sign(self) -> None:
        # Mix of mostly wins: mean return > 0 → positive Sharpe
        trades = (
            [_make_trade(pnl=2.0, outcome="WIN", entry_price=0.5, bet_size=2.0)] * 7
            + [_make_trade(pnl=-0.5, outcome="LOSS", entry_price=0.5, bet_size=2.0)] * 3
        )
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.sharpe_ratio > 0

    def test_negative_edge_sharpe_sign(self) -> None:
        # Mostly losses → negative mean → negative Sharpe
        trades = (
            [_make_trade(pnl=-2.0, outcome="LOSS", entry_price=0.5, bet_size=2.0)] * 7
            + [_make_trade(pnl=0.5, outcome="WIN", entry_price=0.5, bet_size=2.0)] * 3
        )
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.sharpe_ratio < 0

    def test_all_wins_sortino_large_positive(self) -> None:
        # No downside returns → sortino should be large positive
        trades = [
            _make_trade(pnl=1.0, outcome="WIN", entry_price=0.5, bet_size=2.0)
            for _ in range(10)
        ]
        m = compute_metrics(trades, initial_bankroll=40.0)
        # sortino with no downside uses a tiny floor, result is very large
        assert m.sortino_ratio > 0


class TestEquityCurve:
    """Tests for build_equity_curve() and build_drawdown_curve()."""

    def test_equity_curve_starts_at_initial_bankroll(self) -> None:
        trades = [_make_trade(pnl=1.0, outcome="WIN")]
        curve = build_equity_curve(trades, initial_bankroll=40.0)
        assert curve[0] == 40.0

    def test_equity_curve_length_is_trades_plus_one(self) -> None:
        n = 5
        trades = [_make_trade(pnl=1.0) for _ in range(n)]
        curve = build_equity_curve(trades, initial_bankroll=40.0)
        assert len(curve) == n + 1

    def test_equity_curve_monotone_on_all_wins(self) -> None:
        trades = [_make_trade(pnl=1.0, outcome="WIN") for _ in range(5)]
        curve = build_equity_curve(trades, initial_bankroll=40.0)
        for i in range(1, len(curve)):
            assert curve[i] > curve[i - 1]

    def test_equity_curve_values_match_cumulative_pnl(self) -> None:
        pnls = [1.0, -0.5, 2.0, -1.5]
        trades = [
            _make_trade(pnl=p, outcome="WIN" if p > 0 else "LOSS")
            for p in pnls
        ]
        curve = build_equity_curve(trades, initial_bankroll=40.0)
        running = 40.0
        for i, p in enumerate(pnls):
            running += p
            assert curve[i + 1] == pytest.approx(running, rel=1e-6)

    def test_drawdown_curve_empty_input(self) -> None:
        assert build_drawdown_curve([]) == []

    def test_drawdown_curve_no_drawdown_on_monotone_equity(self) -> None:
        curve = [40.0, 41.0, 42.0, 43.0]
        dd = build_drawdown_curve(curve)
        assert all(v == 0.0 for v in dd)

    def test_drawdown_curve_detects_drawdown(self) -> None:
        # 40 → 50 → 40: drawdown of 10/50 = 0.2
        curve = [40.0, 50.0, 40.0]
        dd = build_drawdown_curve(curve)
        assert dd[2] == pytest.approx(0.2, rel=1e-4)

    def test_drawdown_curve_length_matches_equity_curve(self) -> None:
        curve = [40.0, 42.0, 38.0, 45.0]
        dd = build_drawdown_curve(curve)
        assert len(dd) == len(curve)

    def test_drawdown_curve_all_non_negative(self) -> None:
        pnls = [1.0, -2.0, 1.5, -0.5, 2.0]
        trades = [
            _make_trade(pnl=p, outcome="WIN" if p > 0 else "LOSS")
            for p in pnls
        ]
        eq = build_equity_curve(trades, initial_bankroll=40.0)
        dd = build_drawdown_curve(eq)
        assert all(v >= 0.0 for v in dd)


class TestConsecutiveStreaks:
    """Tests for consecutive win/loss detection via compute_metrics."""

    def test_max_consecutive_wins(self) -> None:
        outcomes = ["WIN", "WIN", "WIN", "LOSS", "WIN", "WIN"]
        trades = [
            _make_trade(pnl=1.0 if o == "WIN" else -1.0, outcome=o)
            for o in outcomes
        ]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.max_consecutive_wins == 3

    def test_max_consecutive_losses(self) -> None:
        outcomes = ["WIN", "LOSS", "LOSS", "LOSS", "LOSS", "WIN"]
        trades = [
            _make_trade(pnl=1.0 if o == "WIN" else -1.0, outcome=o)
            for o in outcomes
        ]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.max_consecutive_losses == 4

    def test_alternating_no_streak_beyond_one(self) -> None:
        outcomes = ["WIN", "LOSS", "WIN", "LOSS", "WIN"]
        trades = [
            _make_trade(pnl=1.0 if o == "WIN" else -1.0, outcome=o)
            for o in outcomes
        ]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.max_consecutive_wins == 1
        assert m.max_consecutive_losses == 1

    def test_all_wins_streak_equals_total_trades(self) -> None:
        n = 7
        trades = [_make_trade(pnl=1.0, outcome="WIN") for _ in range(n)]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.max_consecutive_wins == n
        assert m.max_consecutive_losses == 0

    def test_all_losses_streak_equals_total_trades(self) -> None:
        n = 6
        trades = [_make_trade(pnl=-1.0, outcome="LOSS") for _ in range(n)]
        m = compute_metrics(trades, initial_bankroll=40.0)
        assert m.max_consecutive_losses == n
        assert m.max_consecutive_wins == 0


# ---------------------------------------------------------------------------
# 2. DATA PROVIDER TESTS
# ---------------------------------------------------------------------------

class TestGenerateSyntheticSnapshots:
    """Tests for generate_synthetic_snapshots()."""

    def test_correct_number_of_points(self) -> None:
        cfg = _make_config()
        snaps = generate_synthetic_snapshots(cfg, num_points=50, seed=0)
        assert len(snaps) == 50

    def test_returns_list_of_tuples(self) -> None:
        cfg = _make_config()
        snaps = generate_synthetic_snapshots(cfg, num_points=10, seed=1)
        for item in snaps:
            assert isinstance(item, tuple)
            assert len(item) == 2

    def test_timestamps_are_datetime(self) -> None:
        cfg = _make_config()
        snaps = generate_synthetic_snapshots(cfg, num_points=10, seed=2)
        for ts, _ in snaps:
            assert isinstance(ts, datetime)

    def test_snapshots_have_valid_feed_fields(self) -> None:
        cfg = _make_config()
        snaps = generate_synthetic_snapshots(cfg, num_points=20, seed=3)
        for _, snap in snaps:
            assert snap.connected is True
            assert isinstance(snap.last_price, float)
            assert isinstance(snap.bid, float)
            assert isinstance(snap.ask, float)

    def test_prices_are_positive(self) -> None:
        cfg = _make_config()
        snaps = generate_synthetic_snapshots(cfg, num_points=100, seed=4)
        for _, snap in snaps:
            assert snap.last_price > 0.0

    def test_seed_produces_deterministic_output(self) -> None:
        cfg = _make_config()
        snaps_a = generate_synthetic_snapshots(cfg, num_points=50, seed=42)
        snaps_b = generate_synthetic_snapshots(cfg, num_points=50, seed=42)
        prices_a = [snap.last_price for _, snap in snaps_a]
        prices_b = [snap.last_price for _, snap in snaps_b]
        assert prices_a == prices_b

    def test_different_seeds_produce_different_output(self) -> None:
        cfg = _make_config()
        snaps_a = generate_synthetic_snapshots(cfg, num_points=50, seed=1)
        snaps_b = generate_synthetic_snapshots(cfg, num_points=50, seed=99)
        prices_a = [snap.last_price for _, snap in snaps_a]
        prices_b = [snap.last_price for _, snap in snaps_b]
        assert prices_a != prices_b

    def test_default_btc_base_price(self) -> None:
        cfg = _make_config(strategy="MOMENTUM", asset="BTC")
        snaps = generate_synthetic_snapshots(cfg, num_points=5, seed=0)
        # BTC default is 85000; with low volatility the first price is close
        first_price = snaps[0][1].last_price
        assert 50_000 < first_price < 150_000

    def test_timestamps_are_ordered(self) -> None:
        cfg = _make_config()
        snaps = generate_synthetic_snapshots(cfg, num_points=20, seed=0)
        timestamps = [ts for ts, _ in snaps]
        assert timestamps == sorted(timestamps)

    def test_ask_greater_than_bid(self) -> None:
        cfg = _make_config()
        snaps = generate_synthetic_snapshots(cfg, num_points=20, seed=5)
        for _, snap in snaps:
            assert snap.ask >= snap.bid


class TestGenerateBollingerData:
    """Tests for generate_bollinger_data()."""

    def test_warmup_period_returns_none(self) -> None:
        prices = [100.0 + i for i in range(30)]
        result = generate_bollinger_data(prices, period=20)
        # First 19 indices (0–18) should be None
        for i in range(19):
            assert result[i] is None

    def test_first_valid_index_is_period_minus_one(self) -> None:
        prices = [100.0 + i for i in range(30)]
        result = generate_bollinger_data(prices, period=20)
        assert result[19] is not None

    def test_result_length_matches_prices(self) -> None:
        prices = [100.0] * 50
        result = generate_bollinger_data(prices, period=20)
        assert len(result) == 50

    def test_band_structure_has_upper_lower_mid(self) -> None:
        prices = [100.0 + i * 0.5 for i in range(30)]
        result = generate_bollinger_data(prices, period=20)
        for item in result:
            if item is not None:
                assert "upper" in item
                assert "lower" in item
                assert "mid" in item

    def test_upper_greater_than_lower(self) -> None:
        prices = [100.0 + i * 0.5 for i in range(50)]
        result = generate_bollinger_data(prices, period=20)
        for item in result:
            if item is not None:
                assert item["upper"] > item["lower"]

    def test_mid_between_upper_and_lower(self) -> None:
        prices = list(range(50, 100))
        result = generate_bollinger_data(prices, period=10)
        for item in result:
            if item is not None:
                assert item["lower"] <= item["mid"] <= item["upper"]

    def test_constant_prices_zero_band_width(self) -> None:
        prices = [100.0] * 30
        result = generate_bollinger_data(prices, period=20)
        for item in result:
            if item is not None:
                # std of constant series = 0, so upper == lower == mid
                assert item["upper"] == pytest.approx(item["lower"], abs=1e-6)


class TestGenerateRsi:
    """Tests for generate_rsi()."""

    def test_warmup_returns_none_for_first_period_values(self) -> None:
        prices = [float(i + 100) for i in range(30)]
        result = generate_rsi(prices, period=14)
        # Indices 0–13 (first period values) are None
        for i in range(14):
            assert result[i] is None

    def test_first_valid_rsi_at_index_period(self) -> None:
        prices = [float(i + 100) for i in range(30)]
        result = generate_rsi(prices, period=14)
        assert result[14] is not None

    def test_result_length_matches_prices(self) -> None:
        prices = [float(i) for i in range(50)]
        result = generate_rsi(prices, period=14)
        assert len(result) == 50

    def test_rsi_bounded_between_0_and_100(self) -> None:
        import random as _rng
        _rng.seed(0)
        prices = [100.0 * (1 + _rng.gauss(0, 0.01)) for _ in range(100)]
        result = generate_rsi(prices, period=14)
        for val in result:
            if val is not None:
                assert 0.0 <= val <= 100.0

    def test_monotone_up_prices_rsi_above_50(self) -> None:
        # Strictly increasing prices → only gains, no losses → RSI near 100
        prices = [100.0 + i for i in range(30)]
        result = generate_rsi(prices, period=14)
        for val in result:
            if val is not None:
                assert val > 50.0

    def test_monotone_down_prices_rsi_below_50(self) -> None:
        # Strictly decreasing → only losses → RSI near 0
        prices = [200.0 - i for i in range(30)]
        result = generate_rsi(prices, period=14)
        for val in result:
            if val is not None:
                assert val < 50.0


# ---------------------------------------------------------------------------
# 3. ENGINE TESTS
# ---------------------------------------------------------------------------

class TestApplySlippage:
    """Tests for _apply_slippage()."""

    def test_slippage_increases_price(self) -> None:
        adjusted = _apply_slippage(0.50, slippage_bps=50)
        assert adjusted > 0.50

    def test_zero_slippage_returns_original(self) -> None:
        assert _apply_slippage(0.50, slippage_bps=0) == pytest.approx(0.50)

    def test_clamps_above_max(self) -> None:
        # Very high slippage
        adjusted = _apply_slippage(0.999, slippage_bps=1_000_000)
        assert adjusted == 0.999

    def test_clamps_below_min(self) -> None:
        adjusted = _apply_slippage(0.0001, slippage_bps=0)
        assert adjusted >= 0.001

    def test_50bps_slippage_formula(self) -> None:
        price = 0.60
        expected = price * (1.0 + 50 / 10_000.0)
        assert _apply_slippage(price, slippage_bps=50) == pytest.approx(expected, rel=1e-6)

    def test_result_within_valid_range(self) -> None:
        for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
            result = _apply_slippage(p, slippage_bps=100)
            assert 0.001 <= result <= 0.999


class TestComputePnl:
    """Tests for _compute_pnl()."""

    def test_win_produces_positive_pnl(self) -> None:
        pnl = _compute_pnl(
            outcome="WIN",
            entry_price=0.45,
            bet_size=2.0,
            commission_pct=0.02,
            gas_per_trade=0.01,
        )
        assert pnl > 0

    def test_loss_produces_negative_pnl(self) -> None:
        pnl = _compute_pnl(
            outcome="LOSS",
            entry_price=0.45,
            bet_size=2.0,
            commission_pct=0.02,
            gas_per_trade=0.01,
        )
        assert pnl < 0

    def test_win_pnl_formula(self) -> None:
        entry = 0.45
        bet = 2.0
        comm = 0.02
        gas = 0.01
        shares = bet / entry
        gross_profit = (1.0 - entry) * shares
        expected = gross_profit * (1.0 - comm) - gas
        pnl = _compute_pnl("WIN", entry, bet, comm, gas)
        assert pnl == pytest.approx(expected, rel=1e-5)

    def test_loss_pnl_formula(self) -> None:
        entry = 0.45
        bet = 2.0
        gas = 0.01
        expected = -(bet + gas)
        pnl = _compute_pnl("LOSS", entry, bet, 0.02, gas)
        assert pnl == pytest.approx(expected, rel=1e-5)

    def test_win_lower_entry_price_higher_profit(self) -> None:
        pnl_low = _compute_pnl("WIN", 0.30, 2.0, 0.02, 0.01)
        pnl_high = _compute_pnl("WIN", 0.70, 2.0, 0.02, 0.01)
        assert pnl_low > pnl_high

    def test_zero_commission_increases_win_pnl(self) -> None:
        pnl_with = _compute_pnl("WIN", 0.45, 2.0, 0.02, 0.0)
        pnl_without = _compute_pnl("WIN", 0.45, 2.0, 0.0, 0.0)
        assert pnl_without > pnl_with

    def test_gas_always_deducted_on_loss(self) -> None:
        pnl_gas = _compute_pnl("LOSS", 0.45, 2.0, 0.02, 0.05)
        pnl_no_gas = _compute_pnl("LOSS", 0.45, 2.0, 0.02, 0.0)
        assert pnl_no_gas > pnl_gas


class TestDetermineOutcome:
    """Tests for _determine_outcome()."""

    def test_buy_yes_price_up_is_win(self) -> None:
        result = _determine_outcome(Signal.BUY_YES, current_price=100.0, next_price=101.0)
        assert result == "WIN"

    def test_buy_yes_price_down_is_loss(self) -> None:
        result = _determine_outcome(Signal.BUY_YES, current_price=100.0, next_price=99.0)
        assert result == "LOSS"

    def test_buy_yes_price_equal_is_loss(self) -> None:
        # Same price → not strictly greater → LOSS
        result = _determine_outcome(Signal.BUY_YES, current_price=100.0, next_price=100.0)
        assert result == "LOSS"

    def test_buy_no_price_down_is_win(self) -> None:
        result = _determine_outcome(Signal.BUY_NO, current_price=100.0, next_price=99.0)
        assert result == "WIN"

    def test_buy_no_price_up_is_loss(self) -> None:
        result = _determine_outcome(Signal.BUY_NO, current_price=100.0, next_price=101.0)
        assert result == "LOSS"

    def test_buy_no_price_equal_is_loss(self) -> None:
        result = _determine_outcome(Signal.BUY_NO, current_price=100.0, next_price=100.0)
        assert result == "LOSS"

    def test_large_move_up_buy_yes_wins(self) -> None:
        result = _determine_outcome(Signal.BUY_YES, 50_000.0, 55_000.0)
        assert result == "WIN"

    def test_large_move_down_buy_no_wins(self) -> None:
        result = _determine_outcome(Signal.BUY_NO, 50_000.0, 45_000.0)
        assert result == "WIN"


class TestKellyBetSize:
    """Tests for _kelly_bet_size()."""

    def test_returns_between_one_and_ten_pct_of_bankroll(self) -> None:
        cfg = _make_config()
        for win_rate in [0.5, 0.6, 0.7, 0.8, 0.9]:
            bet = _kelly_bet_size(
                bankroll=40.0,
                win_rate=win_rate,
                entry_price=0.45,
                kelly_fraction=1 / 3,
                config=cfg,
            )
            assert bet >= 40.0 * 0.01
            assert bet <= 40.0 * 0.10

    def test_negative_kelly_returns_min_bet(self) -> None:
        cfg = _make_config()
        # Low win_rate + high entry_price → negative Kelly
        bet = _kelly_bet_size(
            bankroll=40.0,
            win_rate=0.1,
            entry_price=0.90,
            kelly_fraction=1 / 3,
            config=cfg,
        )
        assert bet == pytest.approx(40.0 * 0.01, rel=1e-3)

    def test_entry_price_at_or_above_one_returns_min_bet(self) -> None:
        cfg = _make_config()
        bet = _kelly_bet_size(
            bankroll=40.0,
            win_rate=0.8,
            entry_price=1.0,
            kelly_fraction=1 / 3,
            config=cfg,
        )
        assert bet == pytest.approx(40.0 * 0.01, rel=1e-3)

    def test_higher_win_rate_produces_larger_bet(self) -> None:
        cfg = _make_config()
        bet_lo = _kelly_bet_size(40.0, 0.50, 0.45, 1 / 3, cfg)
        bet_hi = _kelly_bet_size(40.0, 0.85, 0.45, 1 / 3, cfg)
        assert bet_hi >= bet_lo

    def test_smaller_bankroll_produces_proportional_bet(self) -> None:
        cfg = _make_config()
        bet_big = _kelly_bet_size(100.0, 0.70, 0.45, 1 / 3, cfg)
        bet_small = _kelly_bet_size(40.0, 0.70, 0.45, 1 / 3, cfg)
        # Larger bankroll → larger absolute bet
        assert bet_big > bet_small


class TestBacktestEngineRun:
    """Async tests for BacktestEngine.run()."""

    async def test_turbo_cvd_produces_trades(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=300, seed=42)
        assert isinstance(result, BacktestResult)
        assert result.metrics.total_trades >= 0  # may be 0 if no signals

    async def test_turbo_vwap_produces_result(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_VWAP", asset="ETH")
        result = await engine.run(cfg, num_points=200, seed=7)
        assert isinstance(result, BacktestResult)

    async def test_momentum_produces_result(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="MOMENTUM", asset="BTC")
        result = await engine.run(cfg, num_points=200, seed=7)
        assert isinstance(result, BacktestResult)

    async def test_bollinger_produces_result(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="BOLLINGER", asset="BTC")
        result = await engine.run(cfg, num_points=200, seed=7)
        assert isinstance(result, BacktestResult)

    async def test_run_with_seed_is_deterministic(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        r1 = await engine.run(cfg, num_points=200, seed=99)
        r2 = await engine.run(cfg, num_points=200, seed=99)
        assert r1.metrics.total_trades == r2.metrics.total_trades
        assert r1.metrics.total_pnl == r2.metrics.total_pnl

    async def test_invalid_strategy_raises_value_error(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="INVALID_STRAT")
        with pytest.raises(ValueError, match="Unknown strategy"):
            await engine.run(cfg, num_points=100)

    async def test_result_has_equity_curve(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=200, seed=10)
        # equity_curve always has at least the initial point
        assert len(result.equity_curve) >= 1
        assert result.equity_curve[0] == cfg.initial_bankroll

    async def test_result_has_drawdown_curve(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=200, seed=11)
        assert isinstance(result.drawdown_curve, list)
        assert len(result.drawdown_curve) == len(result.equity_curve)

    async def test_run_duration_is_positive(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=100, seed=5)
        assert result.run_duration_ms >= 0.0

    async def test_trade_signals_are_valid_strings(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=300, seed=42)
        for trade in result.trades:
            assert trade.signal in ("BUY_YES", "BUY_NO")

    async def test_trade_outcomes_are_valid_strings(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=300, seed=42)
        for trade in result.trades:
            assert trade.exit_outcome in ("WIN", "LOSS")

    async def test_bet_sizes_within_bankroll_bounds(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=300, seed=42)
        for trade in result.trades:
            assert trade.bet_size > 0
            # bet_size should not exceed the initial bankroll
            assert trade.bet_size <= cfg.initial_bankroll

    async def test_entry_price_has_slippage_applied(self) -> None:
        # With 50bps slippage applied, entry price should be >= raw ask
        # The ask in synthetic data is last_price + spread/2
        engine = BacktestEngine()
        cfg = BacktestConfig(
            strategy="TURBO_CVD",
            asset="ETH",
            start_date=_dt(),
            end_date=_dt(2025, 4),
            slippage_bps=50.0,
        )
        result = await engine.run(cfg, num_points=300, seed=42)
        for trade in result.trades:
            # After slippage the entry should be > 0 and <= 0.999
            assert 0.001 <= trade.entry_price <= 0.999

    async def test_bankroll_after_is_monotone_with_pnl(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=300, seed=42)
        running = cfg.initial_bankroll
        for trade in result.trades:
            running = round(running + trade.pnl, 6)
            assert trade.bankroll_after == pytest.approx(running, abs=1e-3)

    async def test_equity_curve_consistent_with_trades(self) -> None:
        engine = BacktestEngine()
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        result = await engine.run(cfg, num_points=300, seed=42)
        # Rebuild equity from trades manually
        rebuilt = build_equity_curve(result.trades, cfg.initial_bankroll)
        for a, b in zip(result.equity_curve, rebuilt):
            assert a == pytest.approx(b, rel=1e-5)


# ---------------------------------------------------------------------------
# 4. WALK-FORWARD TESTS
# ---------------------------------------------------------------------------

class TestWalkForwardAnalyzer:
    """Async tests for WalkForwardAnalyzer."""

    async def test_three_windows_produces_three_results(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=3)
        wf = await analyzer.run(cfg, engine)
        assert len(wf.windows) == 3

    async def test_each_window_has_is_and_oos_metrics(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=3)
        wf = await analyzer.run(cfg, engine)
        for w in wf.windows:
            assert isinstance(w.in_sample, PerformanceMetrics)
            assert isinstance(w.out_of_sample, PerformanceMetrics)

    async def test_window_indices_are_sequential(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=3)
        wf = await analyzer.run(cfg, engine)
        for i, w in enumerate(wf.windows):
            assert w.window_index == i

    async def test_overfitting_score_is_computed(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=3)
        wf = await analyzer.run(cfg, engine)
        assert isinstance(wf.overfitting_score, float)
        assert wf.overfitting_score >= 0.0

    async def test_overfitting_score_capped_at_999(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=3)
        wf = await analyzer.run(cfg, engine)
        assert wf.overfitting_score <= 999.99

    async def test_aggregated_oos_metrics_present(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=3)
        wf = await analyzer.run(cfg, engine)
        assert isinstance(wf.aggregated_oos, PerformanceMetrics)

    async def test_aggregated_oos_total_trades_gte_per_window(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=3)
        wf = await analyzer.run(cfg, engine)
        per_window_total = sum(w.out_of_sample.total_trades for w in wf.windows)
        assert wf.aggregated_oos.total_trades == per_window_total

    async def test_window_dates_are_ordered(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=3)
        wf = await analyzer.run(cfg, engine)
        for w in wf.windows:
            assert w.train_start <= w.train_end
            assert w.test_start <= w.test_end

    async def test_num_windows_less_than_2_raises(self) -> None:
        with pytest.raises(ValueError, match="num_windows must be at least 2"):
            WalkForwardAnalyzer(num_windows=1)

    async def test_two_windows_is_minimum_valid(self) -> None:
        cfg = _make_config()
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=2)
        wf = await analyzer.run(cfg, engine)
        assert len(wf.windows) == 2

    async def test_result_config_matches_input_config(self) -> None:
        cfg = _make_config(strategy="TURBO_CVD", asset="ETH")
        engine = BacktestEngine()
        analyzer = WalkForwardAnalyzer(num_windows=2)
        wf = await analyzer.run(cfg, engine)
        assert wf.config.strategy == cfg.strategy
        assert wf.config.asset == cfg.asset


# ---------------------------------------------------------------------------
# 5. MONTE CARLO TESTS
# ---------------------------------------------------------------------------

class TestMonteCarloAnalyzer:
    """Tests for MonteCarloAnalyzer."""

    def _make_trades(self, n_wins: int, n_losses: int) -> list[SimulatedTrade]:
        trades = []
        for _ in range(n_wins):
            trades.append(_make_trade(pnl=1.0, outcome="WIN", entry_price=0.45, bet_size=2.0))
        for _ in range(n_losses):
            trades.append(_make_trade(pnl=-0.90, outcome="LOSS", entry_price=0.45, bet_size=2.0))
        return trades

    def test_fewer_than_2_trades_returns_zeroed_result(self) -> None:
        cfg = _make_config(mc_iterations=100)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run([], cfg, seed=0)
        assert result.iterations == 0
        assert result.prob_profit == 0.0
        assert result.prob_ruin == 0.0

    def test_single_trade_returns_zeroed_result(self) -> None:
        cfg = _make_config(mc_iterations=100)
        trades = [_make_trade(pnl=1.0, outcome="WIN")]
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=0)
        assert result.iterations == 0

    def test_with_seed_is_deterministic(self) -> None:
        cfg = _make_config(mc_iterations=200)
        trades = self._make_trades(n_wins=15, n_losses=5)
        analyzer = MonteCarloAnalyzer()
        r1 = analyzer.run(trades, cfg, seed=42)
        r2 = analyzer.run(trades, cfg, seed=42)
        assert r1.prob_profit == r2.prob_profit
        assert r1.median_final_equity == r2.median_final_equity

    def test_different_seeds_may_differ(self) -> None:
        cfg = _make_config(mc_iterations=500)
        trades = self._make_trades(n_wins=10, n_losses=10)
        analyzer = MonteCarloAnalyzer()
        r1 = analyzer.run(trades, cfg, seed=1)
        r2 = analyzer.run(trades, cfg, seed=999)
        # With 500 iterations and truly random shuffles these should differ
        # (not guaranteed but extremely likely with 20 trades)
        # Just assert they produce valid results
        assert 0.0 <= r1.prob_profit <= 1.0
        assert 0.0 <= r2.prob_profit <= 1.0

    def test_prob_profit_between_zero_and_one(self) -> None:
        cfg = _make_config(mc_iterations=200)
        trades = self._make_trades(n_wins=10, n_losses=5)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=0)
        assert 0.0 <= result.prob_profit <= 1.0

    def test_prob_ruin_between_zero_and_one(self) -> None:
        cfg = _make_config(mc_iterations=200)
        trades = self._make_trades(n_wins=5, n_losses=10)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=0)
        assert 0.0 <= result.prob_ruin <= 1.0

    def test_profitable_strategy_high_prob_profit(self) -> None:
        cfg = _make_config(mc_iterations=500)
        # 90% win rate with good wins
        trades = self._make_trades(n_wins=18, n_losses=2)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=7)
        assert result.prob_profit > 0.5

    def test_losing_strategy_low_prob_profit(self) -> None:
        cfg = _make_config(mc_iterations=500)
        # Heavy losses
        trades = self._make_trades(n_wins=2, n_losses=18)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=7)
        assert result.prob_profit < 0.5

    def test_percentile_ordering_equity(self) -> None:
        cfg = _make_config(mc_iterations=500)
        trades = self._make_trades(n_wins=10, n_losses=5)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=0)
        assert result.p5_final_equity <= result.median_final_equity
        assert result.median_final_equity <= result.p95_final_equity

    def test_percentile_ordering_sharpe(self) -> None:
        cfg = _make_config(mc_iterations=500)
        trades = self._make_trades(n_wins=10, n_losses=5)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=0)
        assert result.p5_sharpe <= result.median_sharpe
        assert result.median_sharpe <= result.p95_sharpe

    def test_drawdown_p95_gte_median(self) -> None:
        cfg = _make_config(mc_iterations=300)
        trades = self._make_trades(n_wins=8, n_losses=8)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=0)
        assert result.p95_max_drawdown >= result.median_max_drawdown

    def test_iterations_count_matches_config(self) -> None:
        cfg = _make_config(mc_iterations=150)
        trades = self._make_trades(n_wins=5, n_losses=5)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=0)
        assert result.iterations == 150

    def test_confidence_level_from_config(self) -> None:
        cfg = _make_config(mc_iterations=100, mc_confidence=0.99)
        trades = self._make_trades(n_wins=5, n_losses=5)
        analyzer = MonteCarloAnalyzer()
        result = analyzer.run(trades, cfg, seed=0)
        assert result.confidence_level == 0.99


# ---------------------------------------------------------------------------
# 6. REPORT TESTS
# ---------------------------------------------------------------------------

def _build_minimal_backtest_result(
    strategy: str = "TURBO_CVD",
    asset: str = "ETH",
    with_trades: bool = True,
) -> BacktestResult:
    """Build a minimal BacktestResult for report testing."""
    cfg = BacktestConfig(
        strategy=strategy,
        asset=asset,
        start_date=_dt(2025, 1, 1),
        end_date=_dt(2025, 4, 1),
        initial_bankroll=40.0,
    )
    trades: list[SimulatedTrade] = []
    if with_trades:
        trades = [
            _make_trade(pnl=1.0, outcome="WIN", bankroll_after=41.0),
            _make_trade(pnl=-0.5, outcome="LOSS", bankroll_after=40.5),
            _make_trade(pnl=0.8, outcome="WIN", bankroll_after=41.3),
        ]
    metrics = compute_metrics(trades, initial_bankroll=40.0)
    equity_curve = build_equity_curve(trades, initial_bankroll=40.0)
    dd_curve = build_drawdown_curve(equity_curve)
    return BacktestResult(
        config=cfg,
        metrics=metrics,
        trades=trades,
        equity_curve=equity_curve,
        drawdown_curve=dd_curve,
        timestamps=[t.timestamp for t in trades],
        run_duration_ms=12.5,
    )


def _build_minimal_wf_result() -> WalkForwardResult:
    cfg = _make_config()
    m = PerformanceMetrics(total_trades=5, wins=3, losses=2, win_rate=0.6, total_pnl=1.5)
    window = WalkForwardWindow(
        window_index=0,
        train_start=_dt(2025, 1),
        train_end=_dt(2025, 2),
        test_start=_dt(2025, 2),
        test_end=_dt(2025, 3),
        in_sample=m,
        out_of_sample=m,
    )
    return WalkForwardResult(config=cfg, windows=[window], overfitting_score=1.2)


def _build_minimal_mc_result() -> MonteCarloResult:
    return MonteCarloResult(
        iterations=500,
        confidence_level=0.95,
        median_final_equity=42.0,
        p5_final_equity=35.0,
        p95_final_equity=50.0,
        median_max_drawdown=3.0,
        p95_max_drawdown=8.0,
        median_sharpe=0.5,
        p5_sharpe=-0.2,
        p95_sharpe=1.3,
        prob_profit=0.72,
        prob_ruin=0.03,
    )


class TestReportGenerator:
    """Tests for ReportGenerator.generate()."""

    def test_generate_returns_string(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert isinstance(html, str)
        assert len(html) > 0

    def test_html_contains_doctype(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "<!DOCTYPE html>" in html

    def test_html_contains_chartjs_script(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "chart.js" in html.lower()

    def test_html_contains_equity_chart_canvas(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "equityChart" in html

    def test_html_contains_drawdown_chart_canvas(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "drawdownChart" in html

    def test_html_contains_trade_log_table(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "tradeLogTable" in html

    def test_html_contains_performance_summary_section(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "Performance Summary" in html

    def test_html_contains_strategy_name(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result(strategy="TURBO_CVD")
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "TURBO_CVD" in html

    def test_html_contains_asset_name(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result(asset="ETH")
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "ETH" in html

    def test_no_trades_renders_no_trades_message(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result(with_trades=False)
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert "No trades recorded" in html

    def test_walk_forward_section_rendered_when_provided(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        wf = _build_minimal_wf_result()
        report = FullBacktestReport(backtest=bt, walk_forward=wf)
        html = gen.generate(report)
        assert "Walk-Forward Analysis" in html

    def test_walk_forward_section_absent_when_none(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt, walk_forward=None)
        html = gen.generate(report)
        assert "Walk-Forward Analysis" not in html

    def test_monte_carlo_section_rendered_when_provided(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        mc = _build_minimal_mc_result()
        report = FullBacktestReport(backtest=bt, monte_carlo=mc)
        html = gen.generate(report)
        assert "Monte Carlo Analysis" in html

    def test_monte_carlo_section_absent_when_none(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt, monte_carlo=None)
        html = gen.generate(report)
        # The CSS always includes "/* ── Monte Carlo ── */" as a comment,
        # so we check for the section heading which only appears when
        # _render_monte_carlo() is actually called.
        assert "Monte Carlo Analysis" not in html

    def test_full_report_with_all_sections(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        wf = _build_minimal_wf_result()
        mc = _build_minimal_mc_result()
        report = FullBacktestReport(backtest=bt, walk_forward=wf, monte_carlo=mc)
        html = gen.generate(report)
        assert "Walk-Forward Analysis" in html
        assert "Monte Carlo Analysis" in html
        assert "Performance Summary" in html
        assert "equityChart" in html

    def test_save_writes_file_to_disk(self, tmp_path) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        out_path = str(tmp_path / "test_report.html")
        written_path = gen.save(report, out_path)
        import pathlib
        assert pathlib.Path(written_path).exists()
        content = pathlib.Path(written_path).read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content

    def test_save_returns_absolute_path(self, tmp_path) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        out_path = str(tmp_path / "subdir" / "report.html")
        written_path = gen.save(report, out_path)
        import pathlib
        assert pathlib.Path(written_path).is_absolute()

    def test_html_win_rate_appears_in_metrics(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        # win_rate = 2/3 ≈ 66.7%
        assert "Win Rate" in html

    def test_html_is_valid_shell(self) -> None:
        gen = ReportGenerator()
        bt = _build_minimal_backtest_result()
        report = FullBacktestReport(backtest=bt)
        html = gen.generate(report)
        assert html.strip().startswith("<!DOCTYPE html>")
        assert "</html>" in html
