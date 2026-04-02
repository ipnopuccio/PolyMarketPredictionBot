"""Generic CCXT adapter for secondary exchanges.

Provides normalized price ticks via ccxt async. Tries WebSocket
(watch_ticker) first for low-latency updates, falls back to REST polling
if WebSocket is not supported by the exchange.

Supported exchanges: coinbase, kraken, bybit, okx, etc.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import ccxt.async_support as ccxt_async

from bot.feeds.exchange_adapter import ExchangeAdapter, ExchangeHealth, NormalizedTick

logger = logging.getLogger(__name__)

# CCXT symbol mapping: asset -> exchange-specific symbol
DEFAULT_SYMBOLS = {
    "BTC": "BTC/USDT",
    "ETH": "ETH/USDT",
    "SOL": "SOL/USDT",
}

# Some exchanges use different quote currencies
EXCHANGE_SYMBOLS: dict[str, dict[str, str]] = {
    "coinbase": {"BTC": "BTC/USD", "ETH": "ETH/USD", "SOL": "SOL/USD"},
    "kraken": {"BTC": "BTC/USD", "ETH": "ETH/USD", "SOL": "SOL/USD"},
}

REST_POLL_INTERVAL = 5  # seconds between REST polls (fallback)

# Exchanges known to support CCXT watch_ticker via WebSocket
WS_CAPABLE_EXCHANGES = frozenset({
    "binanceus", "bybit", "okx", "kraken", "kucoin", "gate",
    "bitget", "mexc", "htx",
})


class CCXTAdapter(ExchangeAdapter):
    """Secondary exchange adapter using CCXT unified API.

    Tries WebSocket (watch_ticker) first for sub-second updates.
    Falls back to REST polling if WS not supported or fails.
    """

    def __init__(
        self,
        exchange_id: str,
        assets: tuple[str, ...] = ("BTC", "ETH", "SOL"),
        config: dict[str, Any] | None = None,
    ) -> None:
        self._exchange_id = exchange_id
        self._assets = assets
        self._config = config or {}
        self._exchange: Any = None
        self._ticks: dict[str, NormalizedTick] = {}
        self._health = ExchangeHealth(exchange=exchange_id)
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._using_ws = False

        # Resolve symbols for this exchange
        overrides = EXCHANGE_SYMBOLS.get(exchange_id, {})
        self._symbols = {a: overrides.get(a, DEFAULT_SYMBOLS[a]) for a in assets}

    @property
    def name(self) -> str:
        return self._exchange_id

    @property
    def is_primary(self) -> bool:
        return False

    async def start(self) -> None:
        """Initialize CCXT exchange and start data collection."""
        exchange_class = getattr(ccxt_async, self._exchange_id, None)
        if exchange_class is None:
            raise ValueError(f"Unknown exchange: {self._exchange_id}")

        self._exchange = exchange_class({
            "enableRateLimit": True,
            **self._config,
        })

        self._running = True
        self._health.connected = True
        self._health.last_update = time.time()

        # Try WebSocket first for capable exchanges
        if self._exchange_id in WS_CAPABLE_EXCHANGES:
            try:
                task = asyncio.create_task(
                    self._ws_loop(), name=f"ccxt_ws_{self._exchange_id}"
                )
                self._tasks.append(task)
                self._using_ws = True
                logger.info("[CCXTAdapter/%s] Started (WebSocket mode)", self._exchange_id)
                return
            except Exception as e:
                logger.warning(
                    "[CCXTAdapter/%s] WS init failed, falling back to REST: %s",
                    self._exchange_id, e,
                )

        # Fallback: REST polling
        task = asyncio.create_task(
            self._poll_loop(), name=f"ccxt_rest_{self._exchange_id}"
        )
        self._tasks.append(task)
        logger.info("[CCXTAdapter/%s] Started (REST polling mode)", self._exchange_id)

    async def stop(self) -> None:
        """Stop all tasks and close exchange connection."""
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks.clear()

        if self._exchange:
            await self._exchange.close()
            self._exchange = None

        self._health.connected = False
        logger.info("[CCXTAdapter/%s] Stopped", self._exchange_id)

    def get_tick(self, asset: str) -> NormalizedTick | None:
        return self._ticks.get(asset)

    def get_health(self) -> ExchangeHealth:
        return self._health

    # ------------------------------------------------------------------
    # WebSocket mode (preferred)
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Watch tickers via CCXT WebSocket. Falls back to REST on failure."""
        ws_retries = 0
        while self._running:
            try:
                await self._watch_all_tickers()
                ws_retries = 0
            except Exception as e:
                ws_retries += 1
                self._health.error_count += 1
                self._health.last_error = str(e)
                logger.warning(
                    "[CCXTAdapter/%s] WS error (retry %d): %s",
                    self._exchange_id, ws_retries, e,
                )
                if ws_retries > 5:
                    # Too many WS failures — switch to REST permanently
                    logger.warning(
                        "[CCXTAdapter/%s] WS unreliable, switching to REST",
                        self._exchange_id,
                    )
                    self._using_ws = False
                    await self._poll_loop()
                    return
                await asyncio.sleep(min(2 ** ws_retries, 30))

    async def _watch_all_tickers(self) -> None:
        """Watch ticker for each asset via CCXT WebSocket."""
        if not self._exchange:
            return

        # watch_ticker blocks until next update, so we run one per asset
        # in a round-robin fashion
        for asset, symbol in self._symbols.items():
            if not self._running:
                return
            try:
                t0 = time.time()
                ticker = await asyncio.wait_for(
                    self._exchange.watch_ticker(symbol),
                    timeout=10.0,
                )
                latency = (time.time() - t0) * 1000

                self._update_tick(asset, ticker, latency)
            except asyncio.TimeoutError:
                logger.debug(
                    "[CCXTAdapter/%s] WS timeout for %s", self._exchange_id, symbol,
                )
            except Exception as e:
                raise  # propagate to retry handler

    # ------------------------------------------------------------------
    # REST polling mode (fallback)
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Poll tickers via REST. Simple and reliable."""
        retries, backoff = 0, 1.0
        while self._running:
            try:
                await self._fetch_all_tickers()
                self._health.connected = True
                self._health.error_count = 0
                retries, backoff = 0, 1.0
            except Exception as e:
                retries += 1
                self._health.error_count += 1
                self._health.last_error = str(e)
                if retries > 20:
                    self._health.connected = False
                logger.warning(
                    "[CCXTAdapter/%s] Poll error (retry %d): %s",
                    self._exchange_id, retries, e,
                )
                await asyncio.sleep(min(backoff, 30))
                backoff *= 2
                continue

            await asyncio.sleep(REST_POLL_INTERVAL)

    async def _fetch_all_tickers(self) -> None:
        """Fetch ticker for all configured assets via REST."""
        if not self._exchange:
            return

        for asset, symbol in self._symbols.items():
            try:
                t0 = time.time()
                ticker = await self._exchange.fetch_ticker(symbol)
                latency = (time.time() - t0) * 1000
                self._update_tick(asset, ticker, latency)
            except Exception as e:
                logger.debug(
                    "[CCXTAdapter/%s] %s fetch failed: %s",
                    self._exchange_id, symbol, e,
                )

    # ------------------------------------------------------------------
    # Shared
    # ------------------------------------------------------------------

    def _update_tick(self, asset: str, ticker: dict, latency: float) -> None:
        """Update internal tick state from a CCXT ticker response."""
        self._ticks[asset] = NormalizedTick(
            exchange=self._exchange_id,
            asset=asset,
            price=float(ticker.get("last", 0) or 0),
            volume=float(ticker.get("quoteVolume", 0) or 0),
            bid=float(ticker.get("bid", 0) or 0),
            ask=float(ticker.get("ask", 0) or 0),
            timestamp=time.time(),
        )
        self._health.last_update = time.time()
        self._health.latency_ms = latency
        self._health.connected = True
