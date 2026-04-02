"""DexScreener adapter — DEX price aggregator for on-chain price data.

Fetches prices from DexScreener's free REST API which aggregates 80+ DEXes
(Uniswap, Raydium, PancakeSwap, etc.). Used as a secondary price source
alongside CEX data for robust cross-venue consensus.

API docs: https://docs.dexscreener.com/api/reference
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from bot.feeds.exchange_adapter import ExchangeAdapter, ExchangeHealth, NormalizedTick

logger = logging.getLogger(__name__)

# DexScreener API base
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"

# Token addresses for the highest-liquidity pairs per asset
# We use multiple chain/pair combos and pick the most liquid
TOKEN_SEARCH_QUERIES: dict[str, str] = {
    "BTC": "WBTC USDT",
    "ETH": "WETH USDT",
    "SOL": "SOL USDC",
}

# Minimum liquidity (USD) to consider a pair valid
MIN_LIQUIDITY_USD = 100_000

# Poll interval (DexScreener is generous with rate limits)
POLL_INTERVAL = 10  # seconds


class DexScreenerAdapter(ExchangeAdapter):
    """Secondary exchange adapter for DEX prices via DexScreener.

    Aggregates on-chain price data from major DEXes (Uniswap, Raydium,
    PancakeSwap, etc.) via DexScreener's free API.
    """

    def __init__(
        self,
        assets: tuple[str, ...] = ("BTC", "ETH", "SOL"),
    ) -> None:
        self._assets = assets
        self._ticks: dict[str, NormalizedTick] = {}
        self._health = ExchangeHealth(exchange="dexscreener")
        self._running = False
        self._task: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        # Track which DEX provided each price (for logging)
        self._sources: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "dexscreener"

    @property
    def is_primary(self) -> bool:
        return False

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=15,
            headers={"Accept": "application/json"},
        )
        self._running = True
        self._health.connected = True
        self._health.last_update = time.time()
        self._task = asyncio.create_task(
            self._poll_loop(), name="dexscreener_poll"
        )
        logger.info("[DexScreener] Started — tracking %s", ", ".join(self._assets))

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        self._health.connected = False
        logger.info("[DexScreener] Stopped")

    def get_tick(self, asset: str) -> NormalizedTick | None:
        return self._ticks.get(asset)

    def get_health(self) -> ExchangeHealth:
        return self._health

    # ------------------------------------------------------------------
    # Internal polling
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        retries, backoff = 0, 1.0
        while self._running:
            try:
                await self._fetch_all_prices()
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
                    "[DexScreener] Poll error (retry %d): %s", retries, e,
                )
                await asyncio.sleep(min(backoff, 30))
                backoff *= 2
                continue

            await asyncio.sleep(POLL_INTERVAL)

    async def _fetch_all_prices(self) -> None:
        if not self._client:
            return

        for asset in self._assets:
            query = TOKEN_SEARCH_QUERIES.get(asset)
            if not query:
                continue

            try:
                t0 = time.time()
                resp = await self._client.get(
                    f"{DEXSCREENER_API}/search", params={"q": query}
                )
                resp.raise_for_status()
                latency = (time.time() - t0) * 1000

                data = resp.json()
                pairs = data.get("pairs", [])

                tick = self._best_pair_tick(asset, pairs)
                if tick is not None:
                    self._ticks[asset] = tick
                    self._health.last_update = time.time()
                    self._health.latency_ms = latency

            except Exception as e:
                logger.debug("[DexScreener] %s fetch failed: %s", asset, e)

    def _best_pair_tick(
        self, asset: str, pairs: list[dict[str, Any]]
    ) -> NormalizedTick | None:
        """Pick the highest-liquidity pair and extract a NormalizedTick."""
        best: dict[str, Any] | None = None
        best_liq = 0.0

        for pair in pairs:
            liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            price_usd = float(pair.get("priceUsd", 0) or 0)

            if price_usd <= 0 or liq < MIN_LIQUIDITY_USD:
                continue

            if liq > best_liq:
                best = pair
                best_liq = liq

        if best is None:
            return None

        price = float(best.get("priceUsd", 0) or 0)
        volume_24h = float(best.get("volume", {}).get("h24", 0) or 0)
        dex_name = best.get("dexId", "unknown")
        chain = best.get("chainId", "unknown")

        self._sources[asset] = f"{dex_name}/{chain}"

        return NormalizedTick(
            exchange="dexscreener",
            asset=asset,
            price=price,
            volume=volume_24h,
            bid=price * 0.999,   # approximate spread for DEX
            ask=price * 1.001,
            timestamp=time.time(),
        )
