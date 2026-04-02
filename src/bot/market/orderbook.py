"""CLOB orderbook price fetcher with TTL cache."""
from __future__ import annotations

import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)

CLOB_API = "https://clob.polymarket.com"
_CACHE_TTL = 5  # seconds


class OrderbookFetcher:
    """Fetches best ask prices from the Polymarket CLOB orderbook."""

    def __init__(self, cache_ttl: float = _CACHE_TTL) -> None:
        self._cache: dict[str, tuple[float, float]] = {}  # token_id -> (price, ts)
        self._cache_ttl = cache_ttl

    async def get_best_ask(self, token_id: str) -> float | None:
        """Return the best (lowest) ask price for a token, or None."""
        now = time.time()
        cached = self._cache.get(token_id)
        if cached and (now - cached[1]) < self._cache_ttl:
            return cached[0]

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{CLOB_API}/book",
                    params={"token_id": token_id},
                )
                resp.raise_for_status()
                book = resp.json()

            asks = book.get("asks", [])
            if not asks:
                return None

            best = min(float(a["price"]) for a in asks)
            self._cache[token_id] = (best, now)
            return best

        except Exception as exc:
            logger.warning("Failed to fetch orderbook for %s: %s", token_id[:16], exc)
            return None

    @staticmethod
    def parse_token_ids(market: dict) -> tuple[str | None, str | None]:
        """Extract (up_token_id, down_token_id) from a Gamma market dict."""
        # Try clobTokenIds first
        clob_ids = market.get("clobTokenIds", [])
        if isinstance(clob_ids, str):
            try:
                clob_ids = json.loads(clob_ids)
            except Exception:
                clob_ids = []
        if isinstance(clob_ids, list) and len(clob_ids) >= 2:
            return clob_ids[0], clob_ids[1]

        # Fallback to tokens array
        tokens = market.get("tokens", [])
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except Exception:
                tokens = []
        if not tokens:
            return None, None

        up_id: str | None = None
        down_id: str | None = None
        for t in tokens:
            if not isinstance(t, dict):
                continue
            outcome = t.get("outcome", "").lower()
            if outcome in ("yes", "up"):
                up_id = t.get("token_id")
            elif outcome in ("no", "down"):
                down_id = t.get("token_id")

        # Positional fallback
        if not up_id and len(tokens) > 0 and isinstance(tokens[0], dict):
            up_id = tokens[0].get("token_id")
        if not down_id and len(tokens) > 1 and isinstance(tokens[1], dict):
            down_id = tokens[1].get("token_id")

        return up_id, down_id
