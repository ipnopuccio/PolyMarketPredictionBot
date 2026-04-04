"""Regime-aware strategy selector.

Enables/disables strategies based on the current market regime and provides
position-size multipliers. Manual overrides take precedence over regime rules.

Used by main.py as an advisory gate before calling the executor.
"""
from __future__ import annotations

from bot.core.types import RegimeType

# Default rules: which strategies are active per regime
DEFAULT_REGIME_RULES: dict[RegimeType, dict[str, bool]] = {
    RegimeType.TRENDING: {
        "MOMENTUM": True,       # Trend-following works in trends
        "BOLLINGER": False,     # Mean-reversion fails in trends
        "TURBO_CVD": True,      # Order flow works in trends
        "TURBO_VWAP": True,     # VWAP deviation works in trends
    },
    RegimeType.RANGING: {
        "MOMENTUM": False,      # Trend-following whipsaws in ranges
        "BOLLINGER": True,      # Mean-reversion works in ranges
        "TURBO_CVD": True,      # Reduced confidence
        "TURBO_VWAP": True,     # Reduced confidence
    },
    RegimeType.VOLATILE: {
        "MOMENTUM": False,      # Too risky
        "BOLLINGER": False,     # BB too wide
        "TURBO_CVD": True,      # High vol = high CVD = signals
        "TURBO_VWAP": True,     # High vol = VWAP deviation
    },
    RegimeType.UNKNOWN: {
        "MOMENTUM": True,
        "BOLLINGER": True,
        "TURBO_CVD": True,
        "TURBO_VWAP": True,
    },
}


class StrategySelector:
    """Enables/disables strategies based on current market regime.

    Supports manual overrides that take precedence over regime rules.
    """

    def __init__(self, rules: dict[RegimeType, dict[str, bool]] | None = None):
        self._rules = rules or DEFAULT_REGIME_RULES
        self._overrides: dict[str, bool] = {}  # manual on/off per strategy

    def is_allowed(self, strategy_name: str, regime: RegimeType) -> bool:
        """Check if strategy is allowed to trade in the current regime.

        Manual overrides take precedence over regime rules.
        Unknown strategy names are allowed by default.
        """
        if strategy_name in self._overrides:
            return self._overrides[strategy_name]
        regime_rules = self._rules.get(regime, self._rules[RegimeType.UNKNOWN])
        return regime_rules.get(strategy_name, True)

    def set_override(self, strategy_name: str, enabled: bool) -> None:
        """Manually enable/disable a strategy (overrides regime rules)."""
        self._overrides[strategy_name] = enabled

    def clear_override(self, strategy_name: str) -> None:
        """Remove manual override, revert to regime-based rules."""
        self._overrides.pop(strategy_name, None)

    def clear_all_overrides(self) -> None:
        """Remove all manual overrides."""
        self._overrides.clear()

    def get_status(self, regime: RegimeType) -> dict[str, dict]:
        """Return full status for dashboard display.

        For each known strategy, returns whether it is allowed, whether
        there is a manual override, and the size multiplier.
        """
        # Collect all strategy names from rules + overrides
        all_strategies: set[str] = set()
        for regime_rules in self._rules.values():
            all_strategies.update(regime_rules.keys())
        all_strategies.update(self._overrides.keys())

        result: dict[str, dict] = {}
        for name in sorted(all_strategies):
            overridden = name in self._overrides
            result[name] = {
                "allowed": self.is_allowed(name, regime),
                "override": self._overrides[name] if overridden else None,
                "regime_rule": self._rules.get(regime, {}).get(name),
                "size_multiplier": self.get_size_multiplier(name, regime),
            }
        return result

    def get_size_multiplier(self, strategy_name: str, regime: RegimeType) -> float:
        """Return position size multiplier based on regime.

        VOLATILE -> 0.5x (half size for all strategies)
        RANGING  -> 0.8x for turbo strategies, 1.0x otherwise
        Others   -> 1.0x
        """
        if regime == RegimeType.VOLATILE:
            return 0.5
        if regime == RegimeType.RANGING and strategy_name.startswith("TURBO"):
            return 0.8
        return 1.0
