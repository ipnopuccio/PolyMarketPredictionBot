"""Tests for the fractional Kelly position sizer."""
from __future__ import annotations

import pytest

from bot.config import SizerConfig
from bot.execution.sizer import Sizer, _KELLY_CAP
from bot.storage.database import Database
from tests.conftest import insert_resolved_trades


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sizer(
    min_pct: float = 0.01,
    max_pct: float = 0.10,
    default_pct: float = 0.03,
    min_sample: int = 10,
    kelly_fraction: float = 1 / 3,
    rolling_window: int = 50,
) -> Sizer:
    cfg = SizerConfig(
        min_pct=min_pct,
        max_pct=max_pct,
        default_pct=default_pct,
        min_sample=min_sample,
        kelly_fraction=kelly_fraction,
        rolling_window=rolling_window,
    )
    return Sizer(cfg)


STRATEGY = "MOMENTUM"
ASSET = "BTC"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWarmup:
    """During warmup (< min_sample resolved trades) the sizer uses default_pct."""

    async def test_warmup_returns_default_pct(self, db: Database):
        """No resolved trades at all -- should return default_pct * bankroll."""
        sizer = _sizer(default_pct=0.03)
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=100.0)
        # default_pct=3% of 100 = 3.00
        assert size == 3.0

    async def test_warmup_with_partial_trades(self, db: Database):
        """5 resolved trades but min_sample=10 -- still warmup."""
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=3, losses=2)
        sizer = _sizer(min_sample=10)
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=100.0)
        assert size == 3.0  # default_pct * bankroll

    async def test_warmup_exactly_at_min_sample(self, db: Database):
        """Exactly min_sample trades -- should exit warmup and use Kelly."""
        await insert_resolved_trades(db, STRATEGY, ASSET, wins=8, losses=2)
        sizer = _sizer(min_sample=10)
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=100.0)
        # Now we have enough trades: Kelly formula is used (not default_pct)
        # With 80% win rate & entry 0.45 the kelly value is positive
        assert size > 0


class TestKellyFormula:
    """Post-warmup: Kelly formula drives the size between min_s and max_s."""

    async def test_high_win_rate_gives_larger_size(self, db: Database):
        """90% win rate at entry 0.45 should push toward max_s."""
        await insert_resolved_trades(
            db, STRATEGY, ASSET, wins=18, losses=2, entry_price=0.45,
        )
        sizer = _sizer(min_sample=10)
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=100.0)
        min_s = max(100.0 * 0.01, 0.10)  # 1.0
        max_s = max(100.0 * 0.10, min_s)  # 10.0
        assert min_s <= size <= max_s
        # High win rate: should be well above midpoint
        assert size > (min_s + max_s) / 2

    async def test_low_win_rate_returns_min(self, db: Database):
        """30% win rate at entry 0.45 -> negative Kelly -> min_s."""
        await insert_resolved_trades(
            db, STRATEGY, ASSET, wins=3, losses=7, entry_price=0.45,
        )
        sizer = _sizer(min_sample=10)
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=100.0)
        min_s = max(100.0 * 0.01, 0.10)
        assert size == min_s

    async def test_fifty_pct_win_rate(self, db: Database):
        """50% win rate at entry 0.45 should give small positive Kelly."""
        await insert_resolved_trades(
            db, STRATEGY, ASSET, wins=10, losses=10, entry_price=0.45,
        )
        sizer = _sizer(min_sample=10)
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=100.0)
        # wr=0.5, ep=0.45: full_kelly = 0.5 - 0.5*(0.45/0.55) ~ 0.09
        # frac_kelly ~ 0.03, ratio ~ 0.03 / (0.10/3) ~ 0.9
        min_s = max(100.0 * 0.01, 0.10)
        max_s = max(100.0 * 0.10, min_s)
        assert min_s <= size <= max_s


class TestEdgeCases:
    """Bankroll edge cases and clamping."""

    async def test_zero_bankroll_uses_fallback(self, db: Database):
        """bankroll=0 should use the 100.0 fallback."""
        sizer = _sizer()
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=0.0)
        # fallback bankroll=100 => default_pct * 100 = 3.0
        assert size == 3.0

    async def test_negative_bankroll_uses_fallback(self, db: Database):
        """bankroll=-50 should use the 100.0 fallback."""
        sizer = _sizer()
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=-50.0)
        assert size == 3.0

    async def test_entry_price_at_one_returns_min(self, db: Database):
        """If avg entry_price >= 1.0, should return min_s."""
        await insert_resolved_trades(
            db, STRATEGY, ASSET, wins=10, losses=0, entry_price=1.0,
        )
        sizer = _sizer(min_sample=10)
        size = await sizer.kelly_size(db, STRATEGY, ASSET, bankroll=100.0)
        min_s = max(100.0 * 0.01, 0.10)
        assert size == min_s
