"""Typed, validated configuration using pydantic-settings."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class StrategyConfig(BaseSettings):
    """Per-strategy configuration."""
    enabled: bool = True
    max_orders_per_window: int = 10
    signal_interval: int = 15
    max_elapsed_pct: float = 0.85  # skip entries after this % of window


class MomentumConfig(StrategyConfig):
    cvd_threshold: float = 1_000_000
    vwap_threshold: float = 0.0005
    book_imbalance_min: float = 0.1
    max_orders_per_window: int = 10
    max_elapsed_pct: float = 0.70
    # Entry price guards (ported from v1)
    max_entry_buy_yes: float = 0.55
    max_entry_buy_no: float = 0.75



class BollingerConfig(StrategyConfig):
    bb_period: int = 20
    bb_std: float = 1.5  # lowered from 2.0 for more signals
    max_orders_per_window: int = 5
    max_elapsed_pct: float = 0.85


class TurboCvdConfig(StrategyConfig):
    cvd_threshold: float = 200_000
    signal_interval: int = 6
    max_orders_per_window: int = 30
    max_elapsed_pct: float = 0.90


class TurboVwapConfig(StrategyConfig):
    vwap_threshold: float = 0.0002
    signal_interval: int = 6
    max_orders_per_window: int = 30
    max_elapsed_pct: float = 0.90


class FeeConfig(BaseSettings):
    """Realistic fee simulation for paper trading."""
    gas_per_trade: float = 0.01  # Polygon gas cost per trade in USDC
    taker_fee_pct: float = 0.02  # 2% taker fee on winnings (Polymarket standard)
    slippage_bps: float = 50  # 0.5% slippage buffer on entry price


class RiskConfig(BaseSettings):
    """Portfolio-level risk parameters."""
    # Per-strategy drawdown
    strategy_drawdown_disable: float = 0.25  # -25% → disable
    # Portfolio drawdown
    portfolio_drawdown_pause: float = 0.20
    portfolio_pause_minutes: int = 30
    # Correlation
    max_same_direction_per_asset: int = 2
    max_unidirectional_exposure_pct: float = 0.60
    # Circuit breaker
    circuit_breaker_window: int = 30  # rolling N trades
    circuit_breaker_consecutive_losses: int = 5
    circuit_breaker_cooldown_windows: int = 10


class SizerConfig(BaseSettings):
    """Kelly position sizer parameters."""
    min_pct: float = 0.01
    max_pct: float = 0.10
    default_pct: float = 0.03
    min_sample: int = 10
    kelly_fraction: float = 1 / 3
    rolling_window: int = 50


class ExchangeConfig(BaseSettings):
    """Multi-exchange feed configuration."""
    # Secondary CEX exchanges for price consensus
    secondary_exchanges: list[str] = Field(
        default=["coinbase", "kraken", "bybit", "okx"],
    )
    # Enable DexScreener DEX aggregator for on-chain price data
    dex_enabled: bool = True
    # Outlier detection sigma threshold
    outlier_sigma: float = 3.0
    # REST poll interval for secondary exchanges (seconds)
    poll_interval: int = 5


class SecurityConfig(BaseSettings):
    """Security hardening configuration."""
    # CORS allowed origins (comma-separated in .env)
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:3000",
        "http://localhost:5003",
    ])
    # Request body size limit (bytes) — 1MB default
    max_body_size: int = 1_048_576
    # App-level rate limit (requests per minute per IP)
    rate_limit_rpm: int = 120
    # Secondary API key for zero-downtime rotation (empty = disabled)
    api_key_secondary: str = ""


class Settings(BaseSettings):
    """Main application settings."""
    model_config = {"env_prefix": "", "env_file": ".env", "extra": "ignore"}

    mode: str = Field(default="paper", description="Paper trading only")
    db_path: str = Field(default="btc_bot_v2.db")
    db_url: str = Field(default="", description="PostgreSQL connection URL (empty = use SQLite)")
    dashboard_port: int = Field(default=5003)

    assets: list[str] = Field(default=["BTC", "ETH", "SOL"])
    initial_bankroll: float = Field(default=40.0)
    feed_warmup: int = Field(default=30)

    # Per-strategy configs
    momentum: MomentumConfig = Field(default_factory=MomentumConfig)
    bollinger: BollingerConfig = Field(default_factory=BollingerConfig)
    turbo_cvd: TurboCvdConfig = Field(default_factory=TurboCvdConfig)
    turbo_vwap: TurboVwapConfig = Field(default_factory=TurboVwapConfig)

    fees: FeeConfig = Field(default_factory=FeeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    sizer: SizerConfig = Field(default_factory=SizerConfig)
    exchanges: ExchangeConfig = Field(default_factory=ExchangeConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @property
    def strategy_configs(self) -> dict[str, StrategyConfig]:
        return {
            "MOMENTUM": self.momentum,
            "BOLLINGER": self.bollinger,
            "TURBO_CVD": self.turbo_cvd,
            "TURBO_VWAP": self.turbo_vwap,
        }


# Singleton — import and use directly
settings = Settings()
