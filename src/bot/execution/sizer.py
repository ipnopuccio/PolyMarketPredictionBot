"""Fractional Kelly position sizer -- bankroll-aware.

Bet size scales with the current bankroll:
  - Between min_pct and max_pct of current bankroll
  - Kelly formula determines where in the range
  - 1/3 Kelly fraction for safety

Kelly formula for binary markets:
    f* = win_rate - (1 - win_rate) * (entry / (1 - entry))
"""
from __future__ import annotations

import logging

from bot.config import SizerConfig
from bot.storage.database import Database

logger = logging.getLogger(__name__)

# Full Kelly value that maps to max_pct
_KELLY_CAP = 0.10


class Sizer:
    """Kelly-based position sizer."""

    def __init__(self, cfg: SizerConfig) -> None:
        self._cfg = cfg

    async def kelly_size(
        self, db: Database, strategy: str, asset: str, bankroll: float,
    ) -> float:
        """Compute the bet size in USDC for the next trade.

        Returns a value between min_s and max_s (percentages of bankroll),
        defaulting to default_pct during warmup.
        """
        cfg = self._cfg

        if bankroll <= 0:
            bankroll = 100.0  # fallback

        min_s = round(bankroll * cfg.min_pct, 2)
        max_s = round(bankroll * cfg.max_pct, 2)
        def_s = round(bankroll * cfg.default_pct, 2)

        # Floor to avoid trivial bets
        min_s = max(min_s, 0.10)
        max_s = max(max_s, min_s)

        # Warmup: not enough resolved trades
        stats = await db.get_rolling_stats(strategy, asset, n=cfg.rolling_window)
        if stats["resolved"] < cfg.min_sample:
            return round(max(min_s, min(max_s, def_s)), 2)

        wr = stats["win_rate"]
        ep = stats["avg_entry"]
        if ep >= 1.0:
            return min_s

        # Full Kelly
        full_kelly = wr - (1 - wr) * (ep / (1 - ep))
        frac_kelly = full_kelly * cfg.kelly_fraction

        if frac_kelly <= 0:
            return min_s

        # Linear map: frac_kelly=0 -> min_s, frac_kelly=KELLY_CAP*fraction -> max_s
        ratio = min(frac_kelly / (_KELLY_CAP * cfg.kelly_fraction), 1.0)
        size = min_s + (max_s - min_s) * ratio
        return round(max(min_s, min(max_s, size)), 2)
