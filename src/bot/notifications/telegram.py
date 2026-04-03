"""Async Telegram Bot API client with rate limiting.

Sends formatted notifications for trade events, risk alerts, and daily summaries.
No-op if disabled or token is empty — never raises.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Characters that need escaping in MarkdownV2
_ESCAPE_CHARS = r"_*[]()~`>#+-=|{}.!"


def _escape_md(text: str) -> str:
    """Escape special Markdown characters for Telegram."""
    for ch in _ESCAPE_CHARS:
        text = text.replace(ch, f"\\{ch}")
    return text


class TelegramNotifier:
    """Async Telegram notification client with rate limiting."""

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        enabled: bool = False,
        rate_limit_per_min: int = 30,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled and bool(bot_token) and bool(chat_id)
        self._rate_limit = rate_limit_per_min
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ── Core send ──────────────────────────────────────────

    async def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send a message with rate limiting.

        Returns True on success, False on failure or rate limit.
        """
        if not self._enabled:
            return False

        async with self._lock:
            now = time.monotonic()
            # Clean old timestamps outside 60s window
            while self._timestamps and now - self._timestamps[0] > 60:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._rate_limit:
                logger.debug("Telegram rate limit reached (%d/min)", self._rate_limit)
                return False
            self._timestamps.append(now)

        try:
            url = TELEGRAM_API.format(token=self._token)
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json={
                    "chat_id": self._chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                })
                if resp.status_code == 200:
                    return True
                logger.warning("Telegram API error %d: %s", resp.status_code, resp.text[:200])
                return False
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)
            return False

    # ── Pre-formatted notifications ────────────────────────

    async def notify_trade_placed(self, trade_data: dict) -> None:
        strategy = trade_data.get("strategy", "?")
        asset = trade_data.get("asset", "?")
        signal = trade_data.get("signal", "?")
        size = trade_data.get("bet_size", 0)
        conf = trade_data.get("confidence", 0)
        price = trade_data.get("entry_price", 0)

        arrow = "🟢" if signal == "BUY_YES" else "🔴"
        msg = (
            f"{arrow} *TRADE PLACED*\n"
            f"`{strategy}/{asset}` {signal}\n"
            f"Size: ${size:.2f} | Entry: {price:.4f}\n"
            f"Confidence: {conf:.0%}"
        )
        await self.send(msg)

    async def notify_trade_resolved(self, trade_data: dict) -> None:
        outcome = trade_data.get("outcome", "?")
        pnl = trade_data.get("pnl", 0)
        strategy = trade_data.get("strategy", "?")
        asset = trade_data.get("asset", "?")
        signal = trade_data.get("signal", "?")

        icon = "✅" if outcome == "WIN" else "❌"
        sign = "+" if pnl >= 0 else ""
        msg = (
            f"{icon} *{outcome}* {sign}${pnl:.2f}\n"
            f"`{strategy}/{asset}` {signal}"
        )
        await self.send(msg)

    async def notify_circuit_breaker(self, strategy: str, asset: str, cooldown_s: int) -> None:
        minutes = cooldown_s // 60
        msg = (
            f"⚠️ *CIRCUIT BREAKER*\n"
            f"`{strategy}/{asset}` paused for {minutes}min\n"
            f"5 consecutive losses detected"
        )
        await self.send(msg)

    async def notify_drawdown(self, strategy: str, asset: str, drawdown_pct: float) -> None:
        level = "🔴" if drawdown_pct > 0.15 else "🟡"
        msg = (
            f"{level} *DRAWDOWN ALERT*\n"
            f"`{strategy}/{asset}` at -{drawdown_pct:.1%}"
        )
        await self.send(msg)

    async def notify_startup(self, bot_count: int, total_bankroll: float) -> None:
        msg = (
            f"🚀 *BOT STARTED*\n"
            f"{bot_count} bots | ${total_bankroll:.2f} bankroll"
        )
        await self.send(msg)

    async def notify_shutdown(self, reason: str = "manual") -> None:
        msg = f"🛑 *BOT STOPPED*\nReason: {reason}"
        await self.send(msg)

    async def notify_feed_disconnect(self, feed_name: str, duration_s: float) -> None:
        msg = (
            f"📡 *FEED DOWN*\n"
            f"{feed_name} disconnected for {duration_s:.0f}s"
        )
        await self.send(msg)

    async def notify_daily_summary(self, stats: dict) -> None:
        total_pnl = stats.get("total_pnl", 0)
        win_rate = stats.get("win_rate", 0)
        total_trades = stats.get("total_trades", 0)
        bankroll = stats.get("total_bankroll", 0)
        best = stats.get("best_trade", 0)
        worst = stats.get("worst_trade", 0)

        sign = "+" if total_pnl >= 0 else ""
        icon = "📈" if total_pnl >= 0 else "📉"
        msg = (
            f"{icon} *DAILY REPORT*\n"
            f"P&L: {sign}${total_pnl:.2f}\n"
            f"Win Rate: {win_rate:.1f}% | Trades: {total_trades}\n"
            f"Bankroll: ${bankroll:.2f}\n"
            f"Best: +${best:.2f} | Worst: ${worst:.2f}"
        )
        await self.send(msg)
