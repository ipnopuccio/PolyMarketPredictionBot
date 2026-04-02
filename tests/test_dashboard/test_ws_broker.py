"""Tests for WSBroker (in-memory pub/sub for WebSocket streaming)."""
from __future__ import annotations

import asyncio

import pytest

from bot.dashboard.ws_broker import WSBroker, CHANNELS, MAX_QUEUE


# ---------------------------------------------------------------------------
# Client management
# ---------------------------------------------------------------------------

class TestClientManagement:
    def test_add_client(self):
        broker = WSBroker()
        client = broker.add_client("c1")
        assert client.client_id == "c1"
        assert broker.client_count == 1

    def test_add_multiple_clients(self):
        broker = WSBroker()
        broker.add_client("c1")
        broker.add_client("c2")
        assert broker.client_count == 2

    def test_remove_client(self):
        broker = WSBroker()
        broker.add_client("c1")
        broker.remove_client("c1")
        assert broker.client_count == 0

    def test_remove_nonexistent_client(self):
        broker = WSBroker()
        broker.remove_client("nonexistent")  # should not raise
        assert broker.client_count == 0

    def test_get_client(self):
        broker = WSBroker()
        broker.add_client("c1")
        assert broker.get_client("c1") is not None
        assert broker.get_client("c2") is None

    def test_default_channels_all(self):
        broker = WSBroker()
        client = broker.add_client("c1")
        assert client.channels == set(CHANNELS)


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

class TestSubscriptions:
    def test_subscribe_valid_channels(self):
        broker = WSBroker()
        broker.add_client("c1")
        # Unsubscribe all first, then subscribe selectively
        broker.unsubscribe("c1", list(CHANNELS))
        result = broker.subscribe("c1", ["prices", "signals"])
        assert set(result) == {"prices", "signals"}
        assert broker.get_subscriptions("c1") == {"prices", "signals"}

    def test_subscribe_invalid_channel_ignored(self):
        broker = WSBroker()
        broker.add_client("c1")
        broker.unsubscribe("c1", list(CHANNELS))
        result = broker.subscribe("c1", ["prices", "invalid_channel"])
        assert result == ["prices"]

    def test_unsubscribe(self):
        broker = WSBroker()
        broker.add_client("c1")
        broker.unsubscribe("c1", ["prices"])
        subs = broker.get_subscriptions("c1")
        assert "prices" not in subs
        assert "signals" in subs

    def test_subscribe_nonexistent_client(self):
        broker = WSBroker()
        assert broker.subscribe("nonexistent", ["prices"]) == []

    def test_unsubscribe_nonexistent_client(self):
        broker = WSBroker()
        assert broker.unsubscribe("nonexistent", ["prices"]) == []


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

class TestPublish:
    async def test_publish_to_subscribed_client(self):
        broker = WSBroker()
        broker.add_client("c1")
        delivered = await broker.publish("prices", {"price": 67500})
        assert delivered == 1

        msg = await broker.get_message("c1", timeout=1.0)
        assert msg is not None
        assert msg["channel"] == "prices"
        assert msg["data"]["price"] == 67500

    async def test_publish_not_delivered_to_unsubscribed(self):
        broker = WSBroker()
        broker.add_client("c1")
        broker.unsubscribe("c1", ["prices"])
        delivered = await broker.publish("prices", {"price": 67500})
        assert delivered == 0

    async def test_publish_to_multiple_clients(self):
        broker = WSBroker()
        broker.add_client("c1")
        broker.add_client("c2")
        delivered = await broker.publish("signals", {"signal": "BUY_YES"})
        assert delivered == 2

    async def test_publish_invalid_channel(self):
        broker = WSBroker()
        broker.add_client("c1")
        delivered = await broker.publish("nonexistent", {"data": 1})
        assert delivered == 0

    async def test_publish_updates_snapshot(self):
        broker = WSBroker()
        await broker.publish("prices", {"price": 67500})
        snapshot = broker.get_snapshot()
        assert len(snapshot) == 1
        assert snapshot[0]["channel"] == "prices"


# ---------------------------------------------------------------------------
# Message queue
# ---------------------------------------------------------------------------

class TestMessageQueue:
    async def test_get_message_timeout(self):
        broker = WSBroker()
        broker.add_client("c1")
        msg = await broker.get_message("c1", timeout=0.1)
        assert msg is None

    async def test_get_message_nonexistent_client(self):
        broker = WSBroker()
        msg = await broker.get_message("nonexistent", timeout=0.1)
        assert msg is None

    async def test_drain(self):
        broker = WSBroker()
        broker.add_client("c1")
        await broker.publish("prices", {"p": 1})
        await broker.publish("prices", {"p": 2})
        await broker.publish("signals", {"s": "BUY"})

        messages = broker.drain("c1")
        assert len(messages) == 3

        # Queue should be empty after drain
        assert broker.drain("c1") == []

    async def test_drain_nonexistent_client(self):
        broker = WSBroker()
        assert broker.drain("nonexistent") == []

    async def test_queue_overflow_drops_oldest(self):
        broker = WSBroker()
        broker.add_client("c1")
        # Fill queue beyond MAX_QUEUE
        for i in range(MAX_QUEUE + 10):
            await broker.publish("prices", {"i": i})

        messages = broker.drain("c1")
        # Should have at most MAX_QUEUE messages
        assert len(messages) <= MAX_QUEUE


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

class TestSnapshot:
    async def test_snapshot_empty_initially(self):
        broker = WSBroker()
        assert broker.get_snapshot() == []

    async def test_snapshot_tracks_latest_per_channel(self):
        broker = WSBroker()
        await broker.publish("prices", {"price": 67500})
        await broker.publish("prices", {"price": 67600})  # overwrites
        await broker.publish("signals", {"signal": "BUY"})

        snapshot = broker.get_snapshot()
        assert len(snapshot) == 2
        channels = {s["channel"] for s in snapshot}
        assert channels == {"prices", "signals"}
        # Latest price should be 67600
        price_snap = next(s for s in snapshot if s["channel"] == "prices")
        assert price_snap["data"]["price"] == 67600


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_structure(self):
        broker = WSBroker()
        broker.add_client("c1")
        s = broker.summary()
        assert s["clients"] == 1
        assert "channels" in s
        assert "queue_sizes" in s
        assert "c1" in s["queue_sizes"]
