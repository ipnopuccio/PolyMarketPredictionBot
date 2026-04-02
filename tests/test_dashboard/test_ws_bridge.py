"""Tests for WSBridge (EventBus → WSBroker bridge)."""
from __future__ import annotations

import pytest

from bot.core.events import EventBus
from bot.dashboard.ws_broker import WSBroker
from bot.dashboard.ws_bridge import WSBridge


@pytest.fixture
def setup():
    """Create bus, broker, and bridge with one connected client."""
    bus = EventBus()
    broker = WSBroker()
    bridge = WSBridge(bus, broker)
    bridge.install()
    broker.add_client("test")
    return bus, broker


class TestSignalEvent:
    async def test_signal_evaluated_goes_to_signals_channel(self, setup):
        bus, broker = setup
        await bus.publish("signal.evaluated", {
            "strategy": "MOMENTUM",
            "asset": "BTC",
            "signal": "BUY_YES",
            "confidence": 0.82,
        })

        msg = await broker.get_message("test", timeout=1.0)
        assert msg is not None
        assert msg["channel"] == "signals"
        assert msg["data"]["strategy"] == "MOMENTUM"
        assert msg["data"]["confidence"] == 0.82


class TestTradeEvents:
    async def test_trade_placed_goes_to_trades_channel(self, setup):
        bus, broker = setup
        await bus.publish("trade.placed", {
            "trade_id": 42,
            "strategy": "TURBO_CVD",
            "asset": "ETH",
        })

        msg = await broker.get_message("test", timeout=1.0)
        assert msg is not None
        assert msg["channel"] == "trades"
        assert msg["data"]["event"] == "placed"
        assert msg["data"]["trade_id"] == 42

    async def test_trade_resolved_goes_to_trades_channel(self, setup):
        bus, broker = setup
        await bus.publish("trade.resolved", {
            "trade_id": 42,
            "outcome": "WIN",
            "pnl": 0.96,
        })

        msg = await broker.get_message("test", timeout=1.0)
        assert msg is not None
        assert msg["channel"] == "trades"
        assert msg["data"]["event"] == "resolved"

    async def test_trade_event_wraps_non_dict(self, setup):
        bus, broker = setup
        await bus.publish("trade.placed", 42)

        msg = await broker.get_message("test", timeout=1.0)
        assert msg is not None
        assert msg["data"]["trade_id"] == 42
        assert msg["data"]["event"] == "placed"


class TestPriceEvent:
    async def test_price_updated_goes_to_prices_channel(self, setup):
        bus, broker = setup
        await bus.publish("price.updated", {
            "asset": "BTC",
            "price": 67500.0,
        })

        msg = await broker.get_message("test", timeout=1.0)
        assert msg is not None
        assert msg["channel"] == "prices"
        assert msg["data"]["price"] == 67500.0


class TestMetricsEvent:
    async def test_metrics_updated_goes_to_metrics_channel(self, setup):
        bus, broker = setup
        await bus.publish("metrics.updated", {
            "total_pnl": 87.27,
            "win_rate": 0.92,
        })

        msg = await broker.get_message("test", timeout=1.0)
        assert msg is not None
        assert msg["channel"] == "metrics"
        assert msg["data"]["total_pnl"] == 87.27


class TestUnsubscribedClientMissesEvents:
    async def test_unsubscribed_client_does_not_receive(self, setup):
        bus, broker = setup
        broker.unsubscribe("test", ["signals"])
        await bus.publish("signal.evaluated", {"signal": "BUY_YES"})

        msg = await broker.get_message("test", timeout=0.1)
        assert msg is None
