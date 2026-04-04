"""Composite confidence scorer (Phase 12.4).

Replaces single-indicator confidence with a weighted multi-indicator score.
Each component produces a 0.0–1.0 sub-score based on how strongly the
indicator confirms the signal direction.

Default weights:
    RSI alignment:        0.20
    BB position:          0.15
    Funding confirmation: 0.15
    OI trend:             0.15
    L/S ratio:            0.10
    Volume confirmation:  0.15
    Regime alignment:     0.10

Minimum composite threshold (default 0.4) must be met to place a trade.
"""
from __future__ import annotations

from bot.config import CompositeConfidenceConfig
from bot.core.types import FeedSnapshot, RegimeResult, RegimeType


class CompositeConfidenceScorer:
    """Multi-indicator weighted confidence scorer."""

    def __init__(self, cfg: CompositeConfidenceConfig) -> None:
        self._cfg = cfg

    def score(
        self,
        snapshot: FeedSnapshot,
        rsi: float | None = None,
        bb: dict | None = None,
        regime: RegimeResult | None = None,
    ) -> float:
        """Compute composite confidence from all available indicators.

        Returns a value between 0.0 and 1.0.
        """
        cfg = self._cfg
        total = 0.0

        # 1. RSI alignment (0.20): RSI > 50 = bullish, < 50 = bearish
        #    Score = how far RSI is from neutral (50)
        if rsi is not None and 0 < rsi < 100:
            rsi_strength = abs(rsi - 50.0) / 50.0  # 0..1
            total += cfg.w_rsi * rsi_strength
        else:
            total += cfg.w_rsi * 0.5  # neutral if unavailable

        # 2. BB position (0.15): price near or beyond bands = strong signal
        if bb is not None and bb.get("pct") is not None:
            bb_pct = bb["pct"]
            # pct < 0 = below lower band, pct > 1 = above upper band
            bb_strength = min(1.0, abs(bb_pct - 0.5) * 2)
            total += cfg.w_bb * bb_strength
        else:
            total += cfg.w_bb * 0.5

        # 3. Funding rate confirmation (0.15)
        #    Positive funding = longs paying shorts = bullish pressure
        #    Score based on magnitude (typically -0.01 to 0.01)
        funding = snapshot.funding_rate
        if funding != 0:
            funding_strength = min(1.0, abs(funding) / 0.005)
            total += cfg.w_funding * funding_strength
        else:
            total += cfg.w_funding * 0.3

        # 4. OI trend (0.15): high OI = strong conviction
        #    We don't have OI change, so use presence as binary
        oi = snapshot.open_interest
        if oi > 0:
            total += cfg.w_oi * 0.7  # OI present = moderate confidence
        else:
            total += cfg.w_oi * 0.3

        # 5. L/S ratio (0.10): far from 1.0 = directional conviction
        ls = snapshot.long_short_ratio
        if ls > 0:
            ls_strength = min(1.0, abs(ls - 1.0) / 0.5)
            total += cfg.w_ls_ratio * ls_strength
        else:
            total += cfg.w_ls_ratio * 0.5

        # 6. Volume confirmation (0.15): CVD magnitude as proxy
        cvd = abs(snapshot.cvd_2min)
        if cvd > 0:
            # Normalize: 500k is strong, 2M is very strong
            vol_strength = min(1.0, cvd / 1_000_000)
            total += cfg.w_volume * vol_strength
        else:
            total += cfg.w_volume * 0.2

        # 7. Regime alignment (0.10): clear regime = higher confidence
        if regime is not None and regime.regime != RegimeType.UNKNOWN:
            total += cfg.w_regime * regime.confidence
        else:
            total += cfg.w_regime * 0.3

        return round(min(1.0, max(0.0, total)), 4)
