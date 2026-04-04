"""Cross-asset correlation filter (Phase 12.5).

Two rules:
1. BTC drop guard: If BTC drops > X% in 5 minutes, block BUY_YES on ETH/SOL.
2. Correlation filter: If rolling 30-min correlation between BTC and another
   asset is > 0.7 and BTC has a strong directional move, block contrarian
   signals on the correlated asset.

Uses a rolling window of (timestamp, price) per asset to compute returns
and Pearson correlation.
"""
from __future__ import annotations

import logging
import time
from collections import deque

import numpy as np

from bot.config import CorrelationConfig

logger = logging.getLogger(__name__)


class CrossAssetCorrelationFilter:
    """Filters signals based on cross-asset price correlation."""

    def __init__(self, cfg: CorrelationConfig) -> None:
        self._cfg = cfg
        # Per-asset rolling price buffers: deque of (timestamp, price)
        self._prices: dict[str, deque[tuple[float, float]]] = {}

    def _ensure_asset(self, asset: str) -> deque[tuple[float, float]]:
        if asset not in self._prices:
            self._prices[asset] = deque()
        return self._prices[asset]

    def _trim(self, dq: deque[tuple[float, float]], now: float) -> None:
        """Remove entries older than the correlation window."""
        cutoff = now - self._cfg.correlation_window_seconds
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def update(self, asset: str, price: float, timestamp: float) -> None:
        """Record a new price observation."""
        if price <= 0:
            return
        dq = self._ensure_asset(asset)
        self._trim(dq, timestamp)
        dq.append((timestamp, price))

    def _get_recent_return(self, asset: str, window_seconds: int) -> float | None:
        """Compute price return over the last window_seconds."""
        dq = self._prices.get(asset)
        if not dq or len(dq) < 2:
            return None
        now = dq[-1][0]
        cutoff = now - window_seconds
        # Find the oldest price within the window
        old_price = None
        for ts, p in dq:
            if ts >= cutoff:
                old_price = p
                break
        if old_price is None or old_price == 0:
            return None
        return (dq[-1][1] - old_price) / old_price

    def _compute_correlation(self, asset_a: str, asset_b: str) -> float | None:
        """Compute Pearson correlation between two assets over rolling window.

        Uses 1-minute return series derived from the price buffers.
        """
        dq_a = self._prices.get(asset_a)
        dq_b = self._prices.get(asset_b)
        if not dq_a or not dq_b or len(dq_a) < 10 or len(dq_b) < 10:
            return None

        # Build aligned 1-minute return series
        # Bucket prices into 60-second bins
        def _bucket_returns(dq: deque[tuple[float, float]]) -> dict[int, float]:
            buckets: dict[int, list[float]] = {}
            for ts, p in dq:
                bucket = int(ts // 60)
                buckets.setdefault(bucket, []).append(p)
            # Average price per bucket
            avg: dict[int, float] = {k: sum(v) / len(v) for k, v in buckets.items()}
            # Returns
            sorted_keys = sorted(avg.keys())
            returns = {}
            for i in range(1, len(sorted_keys)):
                prev = avg[sorted_keys[i - 1]]
                if prev > 0:
                    returns[sorted_keys[i]] = (avg[sorted_keys[i]] - prev) / prev
            return returns

        ret_a = _bucket_returns(dq_a)
        ret_b = _bucket_returns(dq_b)

        # Find common buckets
        common = sorted(set(ret_a.keys()) & set(ret_b.keys()))
        if len(common) < 5:
            return None

        arr_a = np.array([ret_a[k] for k in common])
        arr_b = np.array([ret_b[k] for k in common])

        std_a = np.std(arr_a)
        std_b = np.std(arr_b)
        if std_a == 0 or std_b == 0:
            return None

        corr = float(np.corrcoef(arr_a, arr_b)[0, 1])
        return corr

    def is_allowed(self, asset: str, signal_direction: str) -> bool:
        """Check if a signal is allowed given cross-asset conditions.

        Returns False if the signal should be blocked.
        """
        # Rule 1: BTC drop guard — block BUY_YES on ETH/SOL if BTC drops
        if asset != "BTC" and signal_direction == "BUY_YES":
            btc_return = self._get_recent_return("BTC", self._cfg.btc_drop_window_seconds)
            if btc_return is not None:
                drop_pct = -btc_return * 100  # positive = BTC dropped
                if drop_pct >= self._cfg.btc_drop_threshold_pct:
                    logger.info(
                        "[CorrelationFilter] BTC dropped %.2f%% in %ds, blocking BUY_YES on %s",
                        drop_pct, self._cfg.btc_drop_window_seconds, asset,
                    )
                    return False

        # Rule 2: Correlation filter — block contrarian signals
        if asset != "BTC":
            corr = self._compute_correlation("BTC", asset)
            if corr is not None and corr > self._cfg.correlation_threshold:
                btc_return = self._get_recent_return("BTC", self._cfg.btc_drop_window_seconds)
                if btc_return is not None:
                    # BTC trending down strongly + high correlation → block BUY_YES
                    if btc_return < -0.005 and signal_direction == "BUY_YES":
                        logger.info(
                            "[CorrelationFilter] BTC↔%s corr=%.2f, BTC down %.2f%%, blocking BUY_YES",
                            asset, corr, btc_return * 100,
                        )
                        return False
                    # BTC trending up strongly + high correlation → block BUY_NO
                    if btc_return > 0.005 and signal_direction == "BUY_NO":
                        logger.info(
                            "[CorrelationFilter] BTC↔%s corr=%.2f, BTC up +%.2f%%, blocking BUY_NO",
                            asset, corr, btc_return * 100,
                        )
                        return False

        return True
