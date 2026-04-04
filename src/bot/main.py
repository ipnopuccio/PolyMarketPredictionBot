"""Main orchestrator for Polymarket Bot v2.

Only runs the 5 profitable bots:
  TURBO_CVD/ETH, TURBO_VWAP/ETH, MOMENTUM/BTC, MOMENTUM/SOL, BOLLINGER/BTC
"""
from __future__ import annotations

import asyncio
import logging
import signal as signal_module
import sys

import uvicorn

from bot.config import settings
from bot.core.events import EventBus
from bot.core.types import ACTIVE_BOTS as ACTIVE_BOT_PAIRS, Signal
from bot.monitoring.metrics import SIGNALS_EVALUATED, SIGNAL_CONFIDENCE, UPTIME_SECONDS
from bot.dashboard.app import create_app
from bot.execution.executor import Executor
from bot.execution.resolver import Resolver
from bot.execution.risk import RiskManager
from bot.execution.sizer import Sizer
from bot.dashboard.ws_broker import WSBroker
from bot.dashboard.ws_bridge import WSBridge
from bot.feeds.binance_ws import BinanceFeed
from bot.feeds.adapters.binance import BinanceAdapter
from bot.feeds.adapters.ccxt_adapter import CCXTAdapter
from bot.feeds.adapters.dexscreener import DexScreenerAdapter
from bot.feeds.exchange_manager import ExchangeManager
from bot.feeds.rsi_feed import RSIFeed
from bot.market.finder import MarketFinder
from bot.market.orderbook import OrderbookFetcher
from bot.storage.database import Database
from bot.storage.factory import create_database
from bot.notifications.telegram import TelegramNotifier
from bot.strategies import (
    BollingerStrategy,
    MomentumStrategy,
    TurboCvdStrategy,
    TurboVwapStrategy,
)
from bot.strategies.adaptive import AdaptiveThreshold
from bot.strategies.multi_tf import MultiTimeframeTrend
from bot.strategies.regime import classify as classify_regime
from bot.strategies.selector import StrategySelector

log = logging.getLogger(__name__)

# ── Active bots (only profitable combinations) ───────────
ACTIVE_BOTS: list[tuple[type, str]] = [
    (TurboCvdStrategy,  "ETH"),   # 88% WR, +$47.92
    (TurboVwapStrategy, "ETH"),   # 83.8% WR, +$19.78
    (MomentumStrategy,  "BTC"),   # 96.4% WR, +$12.80
    (MomentumStrategy,  "SOL"),   # 100% WR, +$6.28
    (BollingerStrategy, "BTC"),   # 100% WR, +$0.49
]

FEED_WARMUP = 30

# Strategy class name -> config key mapping
_CLASS_TO_CONFIG = {
    "TurboCvdStrategy": "TURBO_CVD",
    "TurboVwapStrategy": "TURBO_VWAP",
    "MomentumStrategy": "MOMENTUM",
    "BollingerStrategy": "BOLLINGER",
}


async def run_bot(
    strategy,
    asset: str,
    feed: BinanceFeed,
    rsi_feed: RSIFeed,
    executor: Executor,
    db: Database,
    exchange_mgr: ExchangeManager | None = None,
    bus: EventBus | None = None,
    notifier: TelegramNotifier | None = None,
    adaptive: AdaptiveThreshold | None = None,
    multi_tf: MultiTimeframeTrend | None = None,
    selector: StrategySelector | None = None,
    correlation_filter=None,
    composite_scorer=None,
) -> None:
    """Main loop for a single bot."""
    import time as _time
    name = strategy.name
    cfg = strategy.cfg
    is_scaling = strategy.sizing_mode == "scaling"
    # Price buffer for regime detection (rolling 60 prices ~ 60 signal cycles)
    _price_buf: list[float] = []

    while True:
        try:
            # Use ExchangeManager if available, else direct feed
            if exchange_mgr is not None:
                if not exchange_mgr.is_healthy(asset):
                    await asyncio.sleep(strategy.signal_interval)
                    continue
                snapshot = exchange_mgr.get_snapshot(asset)
            else:
                if not feed.is_healthy(asset):
                    await asyncio.sleep(strategy.signal_interval)
                    continue
                snapshot = feed.get_snapshot(asset)

            # Update RSI feed with latest price
            rsi_feed.update(asset, snapshot.last_price)

            now = _time.time()

            # Phase 12.1: Feed adaptive threshold calculator
            if adaptive is not None and snapshot.last_price > 0:
                adaptive.update(asset, snapshot.cvd_2min, snapshot.vwap_change)

            # Phase 12.2: Feed multi-timeframe trend tracker
            if multi_tf is not None and snapshot.last_price > 0:
                multi_tf.update(asset, snapshot.last_price, now)

            # Phase 12.5: Feed correlation filter
            if correlation_filter is not None and snapshot.last_price > 0:
                correlation_filter.update(asset, snapshot.last_price, now)

            # Build price buffer for regime detection
            if snapshot.last_price > 0:
                _price_buf.append(snapshot.last_price)
                if len(_price_buf) > 200:
                    _price_buf.pop(0)

            # Classify market regime
            regime_result = classify_regime(_price_buf)
            regime_label = regime_result.regime.value

            # Phase 12.3: Check if strategy is allowed in current regime
            if selector is not None and settings.regime_selector.enabled:
                if not selector.is_allowed(name, regime_result.regime):
                    log.debug("[%s/%s] Blocked by regime selector (regime=%s)", name, asset, regime_label)
                    await asyncio.sleep(strategy.signal_interval)
                    continue

            rsi = rsi_feed.get_rsi(asset)
            bb = rsi_feed.get_bollinger(asset)

            result = strategy.evaluate(asset, snapshot, rsi, bb)

            # Phase 12.4: Override confidence with composite score
            if (
                composite_scorer is not None
                and settings.composite_confidence.enabled
                and result.signal != Signal.SKIP
            ):
                composite = composite_scorer.score(
                    snapshot=snapshot,
                    rsi=rsi,
                    bb=bb,
                    regime=regime_result,
                )
                min_conf = settings.composite_confidence.min_confidence
                if composite < min_conf:
                    log.debug(
                        "[%s/%s] Composite confidence %.3f < %.3f, skipping",
                        name, asset, composite, min_conf,
                    )
                    result = strategy._result(Signal.SKIP, 0.0, asset, snapshot, result.indicators)
                else:
                    # Replace confidence with composite
                    result = SignalResult(
                        signal=result.signal,
                        confidence=composite,
                        strategy=result.strategy,
                        asset=result.asset,
                        snapshot=result.snapshot,
                        indicators={**result.indicators, "composite_confidence": composite},
                    )

            # Phase 12.2: Multi-timeframe confirmation filter
            if (
                multi_tf is not None
                and settings.multi_tf.enabled
                and result.signal != Signal.SKIP
            ):
                if not multi_tf.is_confirmed(asset, result.signal.value):
                    log.debug("[%s/%s] Blocked by multi-TF filter (signal=%s)", name, asset, result.signal.value)
                    result = strategy._result(Signal.SKIP, 0.0, asset, snapshot, result.indicators)

            # Phase 12.5: Cross-asset correlation filter
            if (
                correlation_filter is not None
                and settings.correlation.enabled
                and result.signal != Signal.SKIP
            ):
                if not correlation_filter.is_allowed(asset, result.signal.value):
                    log.debug("[%s/%s] Blocked by correlation filter", name, asset)
                    result = strategy._result(Signal.SKIP, 0.0, asset, snapshot, result.indicators)

            # Prometheus metrics
            SIGNALS_EVALUATED.labels(
                strategy=name, asset=asset, signal=result.signal.value,
            ).inc()
            if result.confidence > 0:
                SIGNAL_CONFIDENCE.labels(strategy=name, asset=asset).observe(result.confidence)

            # Compute bb_pct for storage
            bb_pct = bb["pct"] if bb else None

            # Save signal state for dashboard
            mkt_info = {"title": "", "up_price": None}
            await db.save_signal_state(
                strategy=name,
                asset=asset,
                signal=result.signal.value,
                confidence=result.confidence,
                snapshot=snapshot.to_dict(),
                rsi=rsi,
                bb_pct=bb_pct,
                regime=regime_label,
                market_info=mkt_info,
            )

            # Publish real-time events for WebSocket clients
            if bus is not None:
                await bus.publish("price.updated", {
                    "asset": asset,
                    "price": snapshot.last_price,
                    "bid": snapshot.bid,
                    "ask": snapshot.ask,
                })
                await bus.publish("signal.evaluated", {
                    "strategy": name,
                    "asset": asset,
                    "signal": result.signal.value,
                    "confidence": result.confidence,
                })

            if result.signal != Signal.SKIP:
                # Phase 12.3: Apply regime-based size multiplier
                size_mult = 1.0
                if selector is not None and settings.regime_selector.enabled:
                    size_mult = selector.get_size_multiplier(name, regime_result.regime)

                await executor.execute(
                    result,
                    strategy_cfg=cfg,
                    scaling=is_scaling,
                    size_multiplier=size_mult,
                )

        except Exception as e:
            log.error("[%s/%s] Error: %s", name, asset, e, exc_info=True)

        await asyncio.sleep(strategy.signal_interval)


async def metrics_publisher(db: Database, bus: EventBus) -> None:
    """Publish aggregated metrics every 30s for the WS metrics channel."""
    strategy_names = list({_CLASS_TO_CONFIG[cls.__name__] for cls, _ in ACTIVE_BOTS})
    asset_names = list({a for _, a in ACTIVE_BOTS})

    while True:
        await asyncio.sleep(30)
        try:
            all_stats = await db.get_all_stats(strategy_names, asset_names)
            total_pnl = sum(s.get("total_pnl", 0) for s in all_stats)
            total_bankroll = sum(s.get("bankroll", 0) for s in all_stats)
            total_trades = sum(s.get("trades", 0) for s in all_stats)
            total_wins = sum(s.get("wins", 0) for s in all_stats)
            win_rate = (total_wins / total_trades * 100) if total_trades else 0

            await bus.publish("metrics.updated", {
                "total_pnl": round(total_pnl, 2),
                "total_bankroll": round(total_bankroll, 2),
                "total_trades": total_trades,
                "win_rate": round(win_rate, 1),
            })
        except Exception as e:
            log.debug("Metrics publish error: %s", e)


async def equity_snapshot_task(db: Database) -> None:
    """Record equity snapshots every 5 minutes for equity curve charting."""
    strategy_names = list({_CLASS_TO_CONFIG[cls.__name__] for cls, _ in ACTIVE_BOTS})
    asset_names = list({a for _, a in ACTIVE_BOTS})

    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            all_stats = await db.get_all_stats(strategy_names, asset_names)
            for s in all_stats:
                open_trades = await db.get_open_trades(s["strategy"], s["asset"])
                await db.save_equity_snapshot(
                    strategy=s["strategy"],
                    asset=s["asset"],
                    bankroll=s.get("bankroll", 0),
                    total_pnl=s.get("total_pnl", 0),
                    open_trades=len(open_trades),
                )
        except Exception as e:
            log.debug("Equity snapshot error: %s", e)


async def price_recorder(feed: BinanceFeed, db: Database) -> None:
    """Record price history every 60s for dashboard charts."""
    while True:
        await asyncio.sleep(60)
        for asset in settings.assets:
            snapshot = feed.get_snapshot(asset)
            if snapshot.last_price > 0:
                await db.record_price(asset, snapshot.last_price)


async def run_dashboard(
    db: Database,
    broker: WSBroker | None = None,
    exchange_mgr: ExchangeManager | None = None,
    selector: StrategySelector | None = None,
) -> None:
    """Run FastAPI dashboard in the background."""
    app = create_app(db, broker=broker, exchange_mgr=exchange_mgr, selector=selector)
    config = uvicorn.Config(
        app, host="0.0.0.0", port=settings.dashboard_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def print_status(db: Database) -> None:
    """Print leaderboard and exit."""
    await db.connect()
    active_names = set()
    strategy_configs = settings.strategy_configs
    for cls, a in ACTIVE_BOTS:
        config_key = _CLASS_TO_CONFIG.get(cls.__name__)
        cfg = strategy_configs.get(config_key) if config_key else None
        if cfg:
            s = cls(cfg)
            active_names.add((s.name, a))

    strategies = list(strategy_configs.keys())
    all_stats = await db.get_all_stats(strategies, settings.assets)
    await db.close()

    print(f"\n{'Strategy':<14} {'Asset':<6} {'Bankroll':>10} {'Trades':>7} "
          f"{'WR%':>6} {'Edge':>8} {'P&L':>8}")
    print("-" * 65)
    for s in all_stats:
        if (s["strategy"], s["asset"]) not in active_names:
            continue
        print(f"{s['strategy']:<14} {s['asset']:<6} ${s['bankroll']:>8.2f} "
              f"{s['trades']:>7} {s['win_rate']:>5.1f}% "
              f"{s['edge']:>7.4f} ${s['total_pnl']:>7.2f}")


async def main() -> None:
    # Setup structured logging (JSON in Docker, pretty in terminal)
    from bot.monitoring.logging_config import setup_logging
    setup_logging()

    # Status mode
    if "--status" in sys.argv:
        db = await create_database()
        await print_status(db)
        return

    # ── Init components ──────────────────────────────────
    bus = EventBus()
    db = await create_database()

    # Phase 12.1: Adaptive threshold calculator (shared across strategies)
    adaptive_thresh = None
    if settings.adaptive.enabled:
        adaptive_thresh = AdaptiveThreshold(
            window_seconds=settings.adaptive.window_seconds,
            percentile=settings.adaptive.percentile,
            min_samples=settings.adaptive.min_samples,
        )
        log.info("Adaptive thresholds enabled (P%d, %ds window)", settings.adaptive.percentile, settings.adaptive.window_seconds)

    # Phase 12.2: Multi-timeframe trend tracker (shared)
    multi_tf_tracker = None
    if settings.multi_tf.enabled:
        multi_tf_tracker = MultiTimeframeTrend()
        log.info("Multi-timeframe confirmation enabled")

    # Phase 12.3: Regime-based strategy selector (shared)
    strat_selector = None
    if settings.regime_selector.enabled:
        strat_selector = StrategySelector()
        log.info("Regime-based strategy selection enabled")

    # Build strategy instances
    bot_list = []
    active_pairs = []
    strategy_configs = settings.strategy_configs
    for cls, asset in ACTIVE_BOTS:
        config_key = _CLASS_TO_CONFIG.get(cls.__name__)
        cfg = strategy_configs.get(config_key) if config_key else None
        if cfg is None:
            log.error("No config for %s", cls.__name__)
            continue
        # Phase 12.1: Pass adaptive threshold to turbo strategies
        if cls in (TurboCvdStrategy, TurboVwapStrategy) and adaptive_thresh is not None:
            strategy = cls(cfg, adaptive=adaptive_thresh)
        else:
            strategy = cls(cfg)
        bot_list.append((strategy, asset))
        active_pairs.append((strategy.name, asset))

    # Seed bankrolls
    strat_names = list({s.name for s, _ in bot_list})
    bot_assets = list({a for _, a in bot_list})
    await db.seed_bankroll(strat_names, bot_assets, settings.initial_bankroll)

    # Init components
    feed = BinanceFeed()
    rsi = RSIFeed()
    orderbook = OrderbookFetcher()
    market_finder = MarketFinder(orderbook)
    sizer = Sizer(settings.sizer)
    risk = RiskManager(settings.risk)
    executor = Executor(db, market_finder, sizer, risk, bus)
    resolver = Resolver(db, bus)

    # WebSocket broker + EventBus bridge
    broker = WSBroker()
    ws_bridge = WSBridge(bus, broker)
    ws_bridge.install()

    # Telegram notifier
    tg = TelegramNotifier(
        bot_token=settings.telegram.bot_token,
        chat_id=settings.telegram.chat_id,
        enabled=settings.telegram.enabled,
        rate_limit_per_min=settings.telegram.rate_limit_per_min,
    )

    # Wire Telegram to EventBus events
    if tg.is_enabled:
        async def _on_trade_placed(data: dict) -> None:
            await tg.notify_trade_placed(data)
        async def _on_trade_resolved(data: dict) -> None:
            await tg.notify_trade_resolved(data)
        bus.subscribe("trade.placed", _on_trade_placed)
        bus.subscribe("trade.resolved", _on_trade_resolved)
        log.info("Telegram notifications enabled")

    # Multi-exchange setup
    exchange_mgr = ExchangeManager()
    binance_adapter = BinanceAdapter(feed)
    exchange_mgr.add_adapter(binance_adapter)

    for ex_id in settings.exchanges.secondary_exchanges:
        ex_id = ex_id.strip()
        if ex_id:
            adapter = CCXTAdapter(ex_id, tuple(settings.assets))
            exchange_mgr.add_adapter(adapter)
            log.info("Added secondary exchange: %s", ex_id)

    # DEX aggregator (DexScreener)
    if settings.exchanges.dex_enabled:
        dex_adapter = DexScreenerAdapter(tuple(settings.assets))
        exchange_mgr.add_adapter(dex_adapter)
        log.info("Added DexScreener DEX aggregator")

    # Phase 12.4: Composite confidence scorer
    comp_scorer = None
    if settings.composite_confidence.enabled:
        from bot.strategies.composite import CompositeConfidenceScorer
        comp_scorer = CompositeConfidenceScorer(settings.composite_confidence)
        log.info("Composite confidence scoring enabled (min=%.2f)", settings.composite_confidence.min_confidence)

    # Phase 12.5: Cross-asset correlation filter
    corr_filter = None
    if settings.correlation.enabled:
        from bot.strategies.correlation import CrossAssetCorrelationFilter
        corr_filter = CrossAssetCorrelationFilter(settings.correlation)
        log.info("Cross-asset correlation filter enabled")

    # ── Banner ───────────────────────────────────────────
    n = len(bot_list)
    total = settings.initial_bankroll * n
    print(f"\n{'='*58}")
    print(f"  POLYMARKET BOT v2 — {n} Winning Bots")
    print(f"  Mode: {settings.mode.upper()}")
    print(f"  Bankroll: ${total:.2f} totale (${settings.initial_bankroll:.2f}/bot)")
    print(f"  Dashboard: http://localhost:{settings.dashboard_port}")
    print(f"{'='*58}\n")

    # ── Launch tasks ─────────────────────────────────────
    import time as _time
    _start_time = _time.monotonic()

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _shutdown(sig):
        print(f"\n[Bot] {sig.name} — shutting down...")
        stop.set()

    for sig in (signal_module.SIGINT, signal_module.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    tasks: list[asyncio.Task] = []

    # 1. Feed (primary + secondary exchanges)
    log.info("Warming up feeds for %ds...", FEED_WARMUP)
    tasks.append(asyncio.create_task(feed.run(), name="feed"))
    await exchange_mgr.start_all()  # starts secondary adapters
    await asyncio.sleep(FEED_WARMUP)
    log.info("Feeds ready. Exchanges: %d", exchange_mgr.exchange_count)

    # 2. Bot tasks (with Phase 12 intelligence components)
    for strategy, asset in bot_list:
        task = asyncio.create_task(
            run_bot(
                strategy, asset, feed, rsi, executor, db, exchange_mgr, bus, tg,
                adaptive=adaptive_thresh,
                multi_tf=multi_tf_tracker,
                selector=strat_selector,
                correlation_filter=corr_filter,
                composite_scorer=comp_scorer,
            ),
            name=f"bot_{strategy.name}_{asset}",
        )
        tasks.append(task)
        print(f"  > {strategy.name}/{asset} (every {strategy.signal_interval}s)")

    # 3. Resolver
    tasks.append(asyncio.create_task(resolver.run(), name="resolver"))

    # 4. Price recorder
    tasks.append(asyncio.create_task(price_recorder(feed, db), name="price_rec"))

    # 4b. Periodic metrics publisher (every 30s → WS metrics channel)
    tasks.append(asyncio.create_task(metrics_publisher(db, bus), name="metrics_pub"))

    # 4c. Equity snapshot recorder (every 5min)
    tasks.append(asyncio.create_task(equity_snapshot_task(db), name="equity_snap"))

    # 5. Dashboard (with WebSocket broker + exchange health + strategy selector)
    tasks.append(asyncio.create_task(
        run_dashboard(db, broker, exchange_mgr, selector=strat_selector), name="dashboard",
    ))

    print(f"\n[Bot] {n} bot attivi. Dashboard: http://localhost:{settings.dashboard_port}\n")

    # Telegram startup notification
    if tg.is_enabled:
        await tg.notify_startup(n, total)

    # ── Wait for shutdown ────────────────────────────────
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await exchange_mgr.stop_all()
    if tg.is_enabled:
        await tg.notify_shutdown("signal")
    await db.close()
    print("[Bot] Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
