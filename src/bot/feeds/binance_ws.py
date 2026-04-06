"""
Multi-asset Binance WebSocket feed (v2).

Tracks BTC, ETH, SOL via combined streams:
  - aggTrade     -> CVD, price, VWAP change
  - markPrice    -> funding rate, index price
  - forceOrder   -> liquidations
  - bookTicker   -> best bid/ask, book imbalance

REST polling every 30s:
  - /fapi/v1/openInterest                       -> open interest in USDC
  - /futures/data/globalLongShortAccountRatio    -> long/short ratio

Returns immutable FeedSnapshot dataclasses per asset.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

import httpx
import websockets

from bot.core.types import FeedSnapshot

logger = logging.getLogger(__name__)

WINDOW_SECS = 120
REST_BASE = "https://fapi.binance.com"
REST_POLL_INTERVAL = 30

ASSETS = ("BTC", "ETH", "SOL")
SYMBOL_MAP = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}
REST_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}

WS_URL = (
    "wss://fstream.binance.com/stream?streams="
    "btcusdt@aggTrade/btcusdt@markPrice@1s/btcusdt@forceOrder/btcusdt@bookTicker/"
    "ethusdt@aggTrade/ethusdt@markPrice@1s/ethusdt@forceOrder/ethusdt@bookTicker/"
    "solusdt@aggTrade/solusdt@markPrice@1s/solusdt@forceOrder/solusdt@bookTicker"
)


def _empty_state() -> dict[str, Any]:
    return dict(
        last_price=0.0,
        price_2min_ago=0.0,
        vwap_change=0.0,
        cvd_2min=0.0,
        funding_rate=0.0,
        liq_long_2min=0.0,
        liq_short_2min=0.0,
        bid=0.0,
        ask=0.0,
        book_imbalance=0.0,
        open_interest=0.0,
        long_short_ratio=0.0,
        last_update=0.0,
        connected=False,
    )


def _empty_windows() -> dict[str, deque]:
    return dict(trade=deque(), price=deque(), liq=deque())


class BinanceFeed:
    """Async Binance WebSocket + REST aggregator for BTC/ETH/SOL."""

    def __init__(self, max_retries: int = 10) -> None:
        self._max_retries = max_retries
        self._state: dict[str, dict[str, Any]] = {a: _empty_state() for a in ASSETS}
        self._windows: dict[str, dict[str, deque]] = {a: _empty_windows() for a in ASSETS}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_snapshot(self, asset: str) -> FeedSnapshot:
        """Return an immutable FeedSnapshot for the given asset."""
        s = self._state.get(asset)
        if s is None:
            return FeedSnapshot()
        return FeedSnapshot(
            last_price=s["last_price"],
            price_2min_ago=s["price_2min_ago"],
            vwap_change=s["vwap_change"],
            cvd_2min=s["cvd_2min"],
            funding_rate=s["funding_rate"],
            liq_long_2min=s["liq_long_2min"],
            liq_short_2min=s["liq_short_2min"],
            bid=s["bid"],
            ask=s["ask"],
            book_imbalance=s["book_imbalance"],
            open_interest=s["open_interest"],
            long_short_ratio=s["long_short_ratio"],
            connected=s["connected"],
            last_update=s["last_update"],
        )

    def is_healthy(self, asset: str) -> bool:
        """True if the feed is connected and received data within the last 30s."""
        s = self._state.get(asset)
        if s is None:
            return False
        return s["connected"] and (time.time() - s["last_update"] < 30)

    async def run(self) -> None:
        """Run WS + REST poller concurrently.  Runs forever — never gives up."""
        await asyncio.gather(self._ws_loop(), self._rest_poll())

    # ------------------------------------------------------------------
    # WebSocket loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Phase 14: infinite retry loop — never dies after max_retries.

        Backoff caps at 60 s and resets to 1 s on a successful connection.
        """
        attempt, backoff = 0, 1.0
        while True:
            try:
                async with websockets.connect(
                    WS_URL, ping_interval=20, ping_timeout=10
                ) as ws:
                    for s in self._state.values():
                        s["connected"] = True
                    if attempt > 0:
                        logger.info(
                            "[BinanceFeed] Reconnected after %d attempt(s) (BTC+ETH+SOL)",
                            attempt,
                        )
                    else:
                        logger.info("[BinanceFeed] Connected (BTC+ETH+SOL)")
                    attempt, backoff = 0, 1.0  # reset on success

                    async for raw in ws:
                        msg = json.loads(raw)
                        stream = msg.get("stream", "")
                        data = msg.get("data", {})
                        sym = data.get("s", data.get("o", {}).get("s", ""))
                        asset = SYMBOL_MAP.get(sym)
                        if not asset:
                            continue

                        if "aggTrade" in stream:
                            self._handle_agg_trade(asset, data)
                        elif "markPrice" in stream:
                            self._handle_mark_price(asset, data)
                        elif "forceOrder" in stream:
                            self._handle_force_order(asset, data)
                        elif "bookTicker" in stream:
                            self._handle_book_ticker(asset, data)

            except Exception as e:
                for s in self._state.values():
                    s["connected"] = False
                attempt += 1
                logger.warning(
                    "[BinanceFeed] Disconnected (attempt %d): %s — retrying in %.0fs",
                    attempt, e, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ------------------------------------------------------------------
    # REST polling
    # ------------------------------------------------------------------

    async def _rest_poll(self) -> None:
        """Poll Binance REST every 30s for OI and Long/Short ratio."""
        await asyncio.sleep(15)  # stagger from WS startup
        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                for asset, sym in REST_SYMBOLS.items():
                    # Open interest
                    try:
                        resp = await client.get(
                            f"{REST_BASE}/fapi/v1/openInterest",
                            params={"symbol": sym},
                        )
                        oi = float(resp.json().get("openInterest", 0))
                        price = self._state[asset]["last_price"] or 1
                        self._state[asset]["open_interest"] = oi * price
                    except Exception:
                        pass

                    # Long/short ratio
                    try:
                        resp = await client.get(
                            f"{REST_BASE}/futures/data/globalLongShortAccountRatio",
                            params={"symbol": sym, "period": "5m", "limit": 1},
                        )
                        data = resp.json()
                        if data and isinstance(data, list):
                            self._state[asset]["long_short_ratio"] = float(
                                data[0].get("longShortRatio", 1)
                            )
                    except Exception:
                        pass

                await asyncio.sleep(REST_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Stream handlers
    # ------------------------------------------------------------------

    def _trim(self, window: deque, now: float) -> None:
        while window and now - window[0][0] > WINDOW_SECS:
            window.popleft()

    def _handle_agg_trade(self, asset: str, data: dict) -> None:
        now = time.time()
        price = float(data["p"])
        qty = float(data["q"])
        usdc_vol = price * qty
        # m=True -> seller is maker -> sell aggressor -> negative CVD
        net = usdc_vol if not data["m"] else -usdc_vol

        w = self._windows[asset]
        w["trade"].append((now, net))
        w["price"].append((now, price))
        self._trim(w["trade"], now)
        self._trim(w["price"], now)

        s = self._state[asset]
        s["last_price"] = price
        s["cvd_2min"] = sum(v for _, v in w["trade"])
        if w["price"]:
            oldest = w["price"][0][1]
            s["price_2min_ago"] = oldest
            if oldest > 0:
                s["vwap_change"] = (price - oldest) / oldest
        s["last_update"] = now

    def _handle_mark_price(self, asset: str, data: dict) -> None:
        self._state[asset]["funding_rate"] = float(data.get("r", 0) or 0)

    def _handle_book_ticker(self, asset: str, data: dict) -> None:
        bid = float(data.get("b", 0) or 0)
        ask = float(data.get("a", 0) or 0)
        bid_qty = float(data.get("B", 0) or 0)
        ask_qty = float(data.get("A", 0) or 0)
        total = bid_qty + ask_qty

        s = self._state[asset]
        s["bid"] = bid
        s["ask"] = ask
        # Imbalance: +1 = full buy pressure, -1 = full sell pressure
        s["book_imbalance"] = (bid_qty - ask_qty) / total if total > 0 else 0.0

    def _handle_force_order(self, asset: str, data: dict) -> None:
        now = time.time()
        order = data.get("o", {})
        side = order.get("S", "")
        avg_price = float(order.get("ap", 0) or 0)
        qty = float(order.get("z", 0) or 0)
        size_usdc = avg_price * qty

        w = self._windows[asset]
        w["liq"].append((now, side, size_usdc))
        self._trim(w["liq"], now)

        s = self._state[asset]
        # BUY  = engine buying to close SHORT -> short liquidation -> price UP
        # SELL = engine selling to close LONG  -> long liquidation  -> price DOWN
        s["liq_short_2min"] = sum(sz for _, sd, sz in w["liq"] if sd == "BUY")
        s["liq_long_2min"] = sum(sz for _, sd, sz in w["liq"] if sd == "SELL")
