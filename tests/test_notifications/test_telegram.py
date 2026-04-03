"""Tests for Telegram notification client."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from bot.notifications.telegram import TelegramNotifier


# ── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def notifier():
    return TelegramNotifier(
        bot_token="123:FAKETOKEN",
        chat_id="999",
        enabled=True,
        rate_limit_per_min=30,
    )


@pytest.fixture
def disabled_notifier():
    return TelegramNotifier(
        bot_token="123:FAKETOKEN",
        chat_id="999",
        enabled=False,
    )


@pytest.fixture
def mock_response_ok():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True}
    return resp


@pytest.fixture
def mock_response_error():
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "Bad Request"
    return resp


# ── Core send tests ──────────────────────────────────────

class TestSend:
    @pytest.mark.asyncio
    async def test_send_success(self, notifier, mock_response_ok):
        with patch("bot.notifications.telegram.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response_ok
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.send("test message")
            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_api_error(self, notifier, mock_response_error):
        with patch("bot.notifications.telegram.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response_error
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.send("test")
            assert result is False

    @pytest.mark.asyncio
    async def test_send_disabled_noop(self, disabled_notifier):
        result = await disabled_notifier.send("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_empty_token_noop(self):
        n = TelegramNotifier(bot_token="", chat_id="999", enabled=True)
        assert n.is_enabled is False
        result = await n.send("test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_exception_returns_false(self, notifier):
        with patch("bot.notifications.telegram.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = Exception("network error")
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await notifier.send("test")
            assert result is False


# ── Rate limiting tests ──────────────────────────────────

class TestRateLimit:
    @pytest.mark.asyncio
    async def test_rate_limit_blocks_excess(self):
        n = TelegramNotifier(
            bot_token="123:FAKE", chat_id="999",
            enabled=True, rate_limit_per_min=3,
        )
        with patch("bot.notifications.telegram.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            resp = MagicMock()
            resp.status_code = 200
            mock_client.post.return_value = resp
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = []
            for _ in range(5):
                results.append(await n.send("msg"))

            # First 3 should succeed, 4th and 5th rate limited
            assert results[:3] == [True, True, True]
            assert results[3] is False
            assert results[4] is False


# ── Notification formatting tests ────────────────────────

class TestNotifications:
    @pytest.mark.asyncio
    async def test_notify_trade_placed(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_trade_placed({
                "strategy": "MOMENTUM", "asset": "BTC",
                "signal": "BUY_YES", "bet_size": 3.50,
                "confidence": 0.85, "entry_price": 0.52,
            })
            msg = mock_send.call_args[0][0]
            assert "MOMENTUM/BTC" in msg
            assert "BUY_YES" in msg
            assert "$3.50" in msg

    @pytest.mark.asyncio
    async def test_notify_trade_resolved_win(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_trade_resolved({
                "outcome": "WIN", "pnl": 1.20,
                "strategy": "MOMENTUM", "asset": "BTC", "signal": "BUY_YES",
            })
            msg = mock_send.call_args[0][0]
            assert "WIN" in msg
            assert "+$1.20" in msg
            assert "✅" in msg

    @pytest.mark.asyncio
    async def test_notify_trade_resolved_loss(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_trade_resolved({
                "outcome": "LOSS", "pnl": -0.50,
                "strategy": "TURBO_CVD", "asset": "ETH", "signal": "BUY_NO",
            })
            msg = mock_send.call_args[0][0]
            assert "LOSS" in msg
            assert "❌" in msg

    @pytest.mark.asyncio
    async def test_notify_circuit_breaker(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_circuit_breaker("MOMENTUM", "BTC", 3000)
            msg = mock_send.call_args[0][0]
            assert "CIRCUIT BREAKER" in msg
            assert "50min" in msg

    @pytest.mark.asyncio
    async def test_notify_drawdown(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_drawdown("MOMENTUM", "BTC", 0.152)
            msg = mock_send.call_args[0][0]
            assert "DRAWDOWN" in msg
            assert "🔴" in msg

    @pytest.mark.asyncio
    async def test_notify_startup(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_startup(5, 200.0)
            msg = mock_send.call_args[0][0]
            assert "BOT STARTED" in msg
            assert "5 bots" in msg
            assert "$200.00" in msg

    @pytest.mark.asyncio
    async def test_notify_shutdown(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_shutdown("signal")
            msg = mock_send.call_args[0][0]
            assert "BOT STOPPED" in msg

    @pytest.mark.asyncio
    async def test_notify_daily_summary(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_daily_summary({
                "total_pnl": 5.20, "win_rate": 72.0,
                "total_trades": 45, "total_bankroll": 205.20,
                "best_trade": 2.10, "worst_trade": -0.80,
            })
            msg = mock_send.call_args[0][0]
            assert "DAILY REPORT" in msg
            assert "+$5.20" in msg
            assert "72.0%" in msg

    @pytest.mark.asyncio
    async def test_notify_feed_disconnect(self, notifier):
        with patch.object(notifier, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True
            await notifier.notify_feed_disconnect("Binance WS", 65)
            msg = mock_send.call_args[0][0]
            assert "FEED DOWN" in msg
            assert "65s" in msg
