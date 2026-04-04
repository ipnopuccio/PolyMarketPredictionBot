"""Adaptive thresholds — rolling percentile calculator.

Instead of fixed cvd_threshold=1_000_000 or vwap_threshold=0.0005,
computes P75 (configurable) of the rolling 1-hour window of absolute
values.  In calm markets thresholds shrink so signals still fire;
in volatile markets they grow so signals don't over-trigger.

Thread-safe via threading.Lock (called from sync strategy.evaluate).
"""
from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np


class AdaptiveThreshold:
    """Computes adaptive thresholds using rolling percentiles.

    Instead of fixed cvd_threshold=1_000_000, uses P75 of the rolling
    1-hour window of absolute CVD values. Same for VWAP change.
    """

    def __init__(
        self,
        window_seconds: int = 3600,
        percentile: int = 75,
        min_samples: int = 100,
    ) -> None:
        self._window_seconds = window_seconds
        self._percentile = percentile
        self._min_samples = min_samples
        self._lock = threading.Lock()
        # Per-asset deques: (timestamp, abs_value)
        self._cvd: dict[str, deque[tuple[float, float]]] = {}
        self._vwap: dict[str, deque[tuple[float, float]]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_deque(
        self,
        store: dict[str, deque[tuple[float, float]]],
        asset: str,
    ) -> deque[tuple[float, float]]:
        """Return (or create) the deque for *asset*."""
        if asset not in store:
            store[asset] = deque()
        return store[asset]

    def _trim(self, dq: deque[tuple[float, float]], now: float) -> None:
        """Remove entries older than window_seconds."""
        cutoff = now - self._window_seconds
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _percentile_value(
        self,
        dq: deque[tuple[float, float]],
        fallback: float,
    ) -> float:
        """Return the configured percentile of the stored values, or *fallback*."""
        if len(dq) < self._min_samples:
            return fallback
        values = np.array([v for _, v in dq])
        return float(np.percentile(values, self._percentile))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, asset: str, cvd: float, vwap_change: float) -> None:
        """Record a new observation (absolute values are stored)."""
        now = time.time()
        with self._lock:
            cvd_dq = self._get_deque(self._cvd, asset)
            self._trim(cvd_dq, now)
            cvd_dq.append((now, abs(cvd)))

            vwap_dq = self._get_deque(self._vwap, asset)
            self._trim(vwap_dq, now)
            vwap_dq.append((now, abs(vwap_change)))

    def get_cvd_threshold(self, asset: str, fallback: float = 1_000_000) -> float:
        """Return adaptive CVD threshold, or *fallback* if insufficient data."""
        with self._lock:
            dq = self._get_deque(self._cvd, asset)
            self._trim(dq, time.time())
            return self._percentile_value(dq, fallback)

    def get_vwap_threshold(self, asset: str, fallback: float = 0.0005) -> float:
        """Return adaptive VWAP threshold, or *fallback* if insufficient data."""
        with self._lock:
            dq = self._get_deque(self._vwap, asset)
            self._trim(dq, time.time())
            return self._percentile_value(dq, fallback)

    def has_enough_data(self, asset: str) -> bool:
        """True if we have >= min_samples observations for *asset*."""
        with self._lock:
            cvd_dq = self._cvd.get(asset)
            vwap_dq = self._vwap.get(asset)
            if cvd_dq is None or vwap_dq is None:
                return False
            self._trim(cvd_dq, time.time())
            self._trim(vwap_dq, time.time())
            return (
                len(cvd_dq) >= self._min_samples
                and len(vwap_dq) >= self._min_samples
            )
