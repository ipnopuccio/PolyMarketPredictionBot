"""Data provider for backtesting.

Loads historical trade data from the SQLite database and generates
synthetic FeedSnapshots that can be replayed through strategies.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Iterator

import numpy as np

from bot.backtest.models import BacktestConfig
from bot.core.types import FeedSnapshot
from bot.storage.database import Database


async def load_trades_from_db(
    db: Database,
    strategy: str,
    asset: str,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict]:
    """Load resolved trades from the database for a strategy/asset pair."""
    trades = await db.get_trades_for_strategy(strategy, limit=10_000)
    filtered = [
        t for t in trades
        if t["asset"] == asset and t["outcome"] is not None
    ]

    if start_date:
        start_str = start_date.isoformat()
        filtered = [t for t in filtered if t["timestamp"] >= start_str]
    if end_date:
        end_str = end_date.isoformat()
        filtered = [t for t in filtered if t["timestamp"] <= end_str]

    # Reverse to chronological order (DB returns newest first)
    filtered.reverse()
    return filtered


async def load_price_history(
    db: Database, asset: str,
) -> list[dict]:
    """Load price history from database."""
    return await db.get_price_history(asset, limit=200)


def generate_synthetic_snapshots(
    config: BacktestConfig,
    num_points: int = 1000,
    base_price: float | None = None,
    volatility: float = 0.02,
    seed: int | None = None,
) -> list[tuple[datetime, FeedSnapshot]]:
    """Generate synthetic FeedSnapshots for strategy replay.

    Simulates realistic price action with CVD, VWAP, funding,
    liquidation, and orderbook data.

    Args:
        config: Backtest configuration
        num_points: Number of data points to generate
        base_price: Starting price (auto-detected from asset if None)
        volatility: Per-step volatility (as fraction of price)
        seed: Random seed for reproducibility
    """
    if seed is not None:
        np.random.seed(seed)
        random.seed(seed)

    if base_price is None:
        base_price = _default_price(config.asset)

    # Generate price series using geometric Brownian motion
    dt = 1.0 / num_points
    drift = 0.0001  # slight upward bias
    returns = np.random.normal(drift * dt, volatility * np.sqrt(dt), num_points)
    prices = base_price * np.exp(np.cumsum(returns))

    # Generate correlated indicators
    snapshots: list[tuple[datetime, FeedSnapshot]] = []
    interval_sec = _interval_seconds(config.strategy)
    start = config.start_date

    # Rolling state for VWAP
    vwap_prices: list[float] = []
    vwap_volumes: list[float] = []

    for i in range(num_points):
        price = float(prices[i])
        prev_price = float(prices[i - 1]) if i > 0 else price
        ts = start + timedelta(seconds=i * interval_sec)

        # CVD: correlated with price momentum + noise
        momentum = (price - prev_price) / prev_price if prev_price else 0
        cvd = momentum * 5_000_000 + np.random.normal(0, 200_000)

        # VWAP
        volume = abs(np.random.normal(1_000_000, 300_000))
        vwap_prices.append(price * volume)
        vwap_volumes.append(volume)
        if len(vwap_prices) > 120:
            vwap_prices.pop(0)
            vwap_volumes.pop(0)
        vwap = sum(vwap_prices) / sum(vwap_volumes) if sum(vwap_volumes) > 0 else price
        vwap_change = (price - vwap) / vwap if vwap > 0 else 0

        # Funding rate: mean-reverting
        funding = np.random.normal(0.0001, 0.0002)

        # Liquidations: sporadic
        liq_long = abs(np.random.exponential(50_000)) if random.random() < 0.1 else 0
        liq_short = abs(np.random.exponential(50_000)) if random.random() < 0.1 else 0

        # Orderbook
        spread = price * 0.0001
        bid = price - spread / 2
        ask = price + spread / 2
        book_imbalance = np.random.normal(0, 0.3)

        # Open interest
        oi = 500_000_000 + np.random.normal(0, 50_000_000)

        # Long/short ratio
        ls_ratio = max(0.3, min(3.0, np.random.normal(1.0, 0.2)))

        snap = FeedSnapshot(
            last_price=round(price, 2),
            price_2min_ago=round(prev_price, 2),
            vwap_change=round(vwap_change, 6),
            cvd_2min=round(cvd, 2),
            funding_rate=round(funding, 6),
            liq_long_2min=round(liq_long, 2),
            liq_short_2min=round(liq_short, 2),
            bid=round(bid, 2),
            ask=round(ask, 2),
            book_imbalance=round(book_imbalance, 4),
            open_interest=round(oi, 2),
            long_short_ratio=round(ls_ratio, 4),
            connected=True,
            last_update=ts.timestamp(),
        )
        snapshots.append((ts, snap))

    return snapshots


def generate_bollinger_data(
    prices: list[float], period: int = 20, std_mult: float = 1.5,
) -> list[dict | None]:
    """Generate Bollinger Band data from price series.

    Returns list of bb dicts (or None for warmup period).
    """
    results: list[dict | None] = []
    for i in range(len(prices)):
        if i < period - 1:
            results.append(None)
            continue
        window = prices[i - period + 1:i + 1]
        mid = float(np.mean(window))
        std = float(np.std(window, ddof=1))
        results.append({
            "upper": round(mid + std_mult * std, 2),
            "lower": round(mid - std_mult * std, 2),
            "mid": round(mid, 2),
        })
    return results


def generate_rsi(prices: list[float], period: int = 14) -> list[float | None]:
    """Generate RSI-14 from price series."""
    results: list[float | None] = []
    for i in range(len(prices)):
        if i < period:
            results.append(None)
            continue
        deltas = [prices[j] - prices[j - 1] for j in range(i - period + 1, i + 1)]
        gains = [d for d in deltas if d > 0]
        losses = [abs(d) for d in deltas if d < 0]
        avg_gain = sum(gains) / period if gains else 0.0001
        avg_loss = sum(losses) / period if losses else 0.0001
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        results.append(round(rsi, 2))
    return results


def _default_price(asset: str) -> float:
    """Default starting price by asset."""
    defaults = {"BTC": 85000.0, "ETH": 2000.0, "SOL": 140.0}
    return defaults.get(asset, 1000.0)


def _interval_seconds(strategy: str) -> int:
    """Interval between data points based on strategy."""
    fast = {"TURBO_CVD", "TURBO_VWAP"}
    return 6 if strategy in fast else 15
