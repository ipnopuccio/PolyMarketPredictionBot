"""Async market finder for Polymarket Up/Down windows.

Markets rotate every 5 minutes (BTC/ETH) or 15 minutes (SOL).
Slug format: "{asset}-updown-{interval}m-{unix_timestamp}"
Resolution: Up wins if price at end >= price at start (Chainlink oracle).
  outcome[0] = Up  = BUY_YES
  outcome[1] = Down = BUY_NO
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from bot.core.types import MarketInfo, Signal
from bot.market.orderbook import OrderbookFetcher

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"

# Slug prefix and interval (seconds) per asset
MARKET_SERIES: dict[str, dict] = {
    "BTC": {"prefix": "btc-updown-5m", "interval": 300},
    "ETH": {"prefix": "eth-updown-5m", "interval": 300},
    "SOL": {"prefix": "sol-updown-15m", "interval": 900},
}


class MarketFinder:
    """Discovers and caches active Polymarket Up/Down markets."""

    def __init__(self, orderbook: OrderbookFetcher | None = None) -> None:
        self._orderbook = orderbook or OrderbookFetcher()
        # Cache: asset -> (MarketInfo, window_start_ts)
        self._cache: dict[str, tuple[MarketInfo, int]] = {}

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _window_ts(asset: str) -> int:
        """Current window-aligned unix timestamp for the given asset."""
        interval = MARKET_SERIES[asset]["interval"]
        return (int(time.time()) // interval) * interval

    @staticmethod
    def _parse_prices(market: dict) -> list[float]:
        raw = market.get("outcomePrices", [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return []
        try:
            return [float(p) for p in raw]
        except Exception:
            return []

    # ── Public API ──────────────────────────────────────────

    async def find_market(self, asset: str, retries: int = 3) -> MarketInfo | None:
        """Find the currently active Up/Down market for *asset*.

        Tries the current window timestamp, then +-interval for edge-cases
        where the market opens slightly late.  Results are cached per window.
        """
        if asset not in MARKET_SERIES:
            return None

        series = MARKET_SERIES[asset]
        ts = self._window_ts(asset)

        # Cache hit for the same window
        cached = self._cache.get(asset)
        if cached and cached[1] == ts:
            return cached[0]

        prefix = series["prefix"]
        interval = series["interval"]

        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    for delta in (0, interval, -interval):
                        candidate_ts = ts + delta
                        slug = f"{prefix}-{candidate_ts}"
                        resp = await client.get(
                            f"{GAMMA_API}/events",
                            params={"slug": slug},
                        )
                        resp.raise_for_status()
                        events = resp.json()
                        if not events:
                            continue

                        event = events[0]
                        markets = event.get("markets", [])
                        if not markets:
                            mresp = await client.get(
                                f"{GAMMA_API}/markets",
                                params={
                                    "event_id": event["id"],
                                    "active": "true",
                                    "closed": "false",
                                    "limit": 1,
                                },
                            )
                            markets = mresp.json() if mresp.status_code == 200 else []

                        if not markets:
                            continue

                        market = markets[0]
                        if market.get("closed") or not market.get("active", True):
                            continue

                        prices = self._parse_prices(market)
                        if not prices or len(prices) < 2:
                            continue

                        up_id, down_id = OrderbookFetcher.parse_token_ids(market)

                        info = MarketInfo(
                            asset=asset,
                            market_id=market.get("id", ""),
                            event_title=event.get("title", slug),
                            up_token_id=up_id,
                            down_token_id=down_id,
                            up_price=prices[0],
                            down_price=prices[1],
                            window_start=candidate_ts,
                            interval=interval,
                        )
                        self._cache[asset] = (info, ts)
                        logger.info(
                            "%s -> '%s' Up=%.3f Down=%.3f",
                            asset, info.event_title, info.up_price, info.down_price,
                        )
                        return info

            except Exception as exc:
                wait = 2 ** attempt
                logger.warning(
                    "%s lookup failed (attempt %d): %s", asset, attempt + 1, exc,
                )
                if attempt < retries - 1:
                    await asyncio.sleep(wait)

        logger.info("No active Up/Down market found for %s", asset)
        return None

    async def get_entry_price(self, market: MarketInfo, signal: Signal) -> float:
        """Best ask from CLOB, falling back to outcomePrices mid-market.

        signal == BUY_YES -> betting Up  -> ask on Up token
        signal == BUY_NO  -> betting Down -> ask on Down token
        """
        is_yes = signal == Signal.BUY_YES
        token_id = market.up_token_id if is_yes else market.down_token_id

        if token_id:
            ask = await self._orderbook.get_best_ask(token_id)
            if ask is not None and 0 < ask < 1:
                return ask

        # Fallback to mid-market from cached outcomePrices
        return market.up_price if is_yes else market.down_price

    def window_elapsed_pct(self, asset: str) -> float:
        """Fraction of the current window that has elapsed (0.0 - 1.0)."""
        interval = MARKET_SERIES.get(asset, {}).get("interval", 300)
        now = int(time.time())
        window_start = (now // interval) * interval
        return (now - window_start) / interval
