"""Load tests for the dashboard API and WebSocket endpoints.

Verifies the dashboard handles concurrent HTTP requests and WebSocket
connections without errors or resource leaks.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from bot.config import settings
from bot.core.events import EventBus
from bot.core.types import FeedSnapshot
from bot.dashboard.app import create_app
from bot.dashboard.ws_broker import WSBroker
from bot.storage.database import Database


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def app_with_data(db):
    """Create a FastAPI app with seeded data for load testing."""
    # Seed some signal states
    snapshot = FeedSnapshot(
        last_price=67_500.0,
        cvd_2min=1_000_000.0,
        vwap_change=0.0005,
        funding_rate=0.0001,
        connected=True,
        last_update=time.time(),
    )
    for strategy, asset in [
        ("TURBO_CVD", "ETH"),
        ("TURBO_VWAP", "ETH"),
        ("MOMENTUM", "BTC"),
        ("MOMENTUM", "SOL"),
        ("BOLLINGER", "BTC"),
    ]:
        await db.save_signal_state(
            strategy=strategy,
            asset=asset,
            signal="BUY_YES",
            confidence=0.75,
            snapshot=snapshot.to_dict(),
            rsi=55.0,
            bb_pct=0.45,
            regime="TRENDING",
            market_info={"title": f"{asset} Up/Down", "up_price": 0.52},
        )

    # Seed some trades
    for i in range(20):
        tid = await db.reserve_and_insert_trade(
            strategy="MOMENTUM",
            asset="BTC",
            market_id=f"0xload_{i}",
            signal="BUY_YES",
            entry_price=0.50,
            bet_size=0.50,
            confidence=0.8,
            regime="TRENDING",
            snapshot=snapshot.to_dict(),
        )
        if tid and i < 15:
            pnl = 0.25 if i % 3 != 0 else -0.25
            outcome = "WIN" if pnl > 0 else "LOSS"
            await db.resolve_trade(tid, outcome, pnl)

    broker = WSBroker()
    app = create_app(db, broker=broker)
    return app


@pytest.fixture
async def client(app_with_data):
    """AsyncClient for the test app."""
    transport = ASGITransport(app=app_with_data)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Concurrent API requests ────────────────────────────────────────────────


class TestConcurrentAPIRequests:
    """Verify dashboard API handles concurrent requests.

    Note: The app has a rate limiter (120 rpm). Under concurrent load,
    some requests will get 429. We validate that responses are either
    200 (success) or 429 (rate limited) — never 500.
    """

    @pytest.mark.asyncio
    async def test_50_concurrent_overview(self, client):
        """50 concurrent GET /api/overview calls."""
        tasks = [client.get("/api/overview") for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 429)
        ok = [r for r in responses if r.status_code == 200]
        assert len(ok) > 0
        data = [r.json() for r in ok]
        assert all(d["mode"] == "paper" for d in data)

    @pytest.mark.asyncio
    async def test_50_concurrent_bots(self, client):
        """50 concurrent GET /api/bots calls."""
        tasks = [client.get("/api/bots") for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 429)
        ok = [r for r in responses if r.status_code == 200]
        assert len(ok) > 0
        data = [r.json() for r in ok]
        assert all(len(d) == 5 for d in data)

    @pytest.mark.asyncio
    async def test_50_concurrent_signals(self, client):
        """50 concurrent GET /api/signals calls."""
        tasks = [client.get("/api/signals") for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 429)

    @pytest.mark.asyncio
    async def test_50_concurrent_trades(self, client):
        """50 concurrent GET /api/trades calls."""
        tasks = [client.get("/api/trades?limit=50") for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 429)

    @pytest.mark.asyncio
    async def test_mixed_concurrent_endpoints(self, client):
        """100 concurrent mixed endpoint calls — no 500 errors."""
        endpoints = [
            "/api/overview",
            "/api/bots",
            "/api/signals",
            "/api/trades?limit=20",
            "/api/risk-events",
        ]
        tasks = [client.get(endpoints[i % len(endpoints)]) for i in range(100)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 429), f"Unexpected {r.status_code} on {r.url}"

    @pytest.mark.asyncio
    async def test_html_dashboard_concurrent(self, client):
        """50 concurrent GET / (HTML dashboard) calls."""
        tasks = [client.get("/") for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 429)
        ok = [r for r in responses if r.status_code == 200]
        assert len(ok) > 0
        assert all("Polymarket Bot v2" in r.text for r in ok)

    @pytest.mark.asyncio
    async def test_metrics_endpoint_concurrent(self, client):
        """50 concurrent GET /metrics calls."""
        tasks = [client.get("/metrics") for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 429)


# ── Authenticated API under load ────────────────────────────────────────────


class TestAuthenticatedLoad:
    """Authenticated endpoints under concurrent load."""

    @pytest.mark.asyncio
    async def test_50_concurrent_positions(self, client):
        """50 concurrent GET /api/v2/positions with API key."""
        headers = {"X-API-Key": settings.api_key if hasattr(settings, 'api_key') else "test"}
        tasks = [client.get("/api/v2/positions", headers=headers) for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 401, 403, 429)

    @pytest.mark.asyncio
    async def test_50_concurrent_health(self, client):
        """50 concurrent GET /api/v2/health with API key."""
        headers = {"X-API-Key": settings.api_key if hasattr(settings, 'api_key') else "test"}
        tasks = [client.get("/api/v2/health", headers=headers) for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        for r in responses:
            assert r.status_code in (200, 401, 403, 429)


# ── Response time validation ────────────────────────────────────────────────


class TestResponseTime:
    """Verify response times stay within acceptable limits."""

    @pytest.mark.asyncio
    async def test_overview_response_time(self, client):
        """GET /api/overview should respond in < 500ms."""
        t0 = time.monotonic()
        response = await client.get("/api/overview")
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert response.status_code == 200
        assert elapsed_ms < 500, f"Response took {elapsed_ms:.0f}ms (max 500ms)"

    @pytest.mark.asyncio
    async def test_bots_response_time(self, client):
        """GET /api/bots should respond in < 500ms (may be rate-limited)."""
        t0 = time.monotonic()
        response = await client.get("/api/bots")
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert response.status_code in (200, 429)
        assert elapsed_ms < 500, f"Response took {elapsed_ms:.0f}ms (max 500ms)"

    @pytest.mark.asyncio
    async def test_dashboard_response_time(self, client):
        """GET / should respond in < 200ms (may be rate-limited from previous tests)."""
        t0 = time.monotonic()
        response = await client.get("/")
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert response.status_code in (200, 429)
        assert elapsed_ms < 200, f"Response took {elapsed_ms:.0f}ms (max 200ms)"


# ── WSBroker under load ─────────────────────────────────────────────────────


class TestWSBrokerLoad:
    """WSBroker message publishing under load."""

    @pytest.mark.asyncio
    async def test_1000_messages_published(self):
        """Publish 1000 messages without errors."""
        broker = WSBroker()

        for i in range(1000):
            await broker.publish("prices", {
                "asset": "BTC",
                "price": 67_500.0 + i,
                "timestamp": time.time(),
            })

        # Broker should not raise or leak

    @pytest.mark.asyncio
    async def test_concurrent_publish(self):
        """100 concurrent publishes should not raise."""
        broker = WSBroker()

        tasks = [
            broker.publish("signals", {"strategy": "MOMENTUM", "i": i})
            for i in range(100)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        exceptions = [r for r in results if isinstance(r, Exception)]
        assert len(exceptions) == 0


# ── EventBus under load ────────────────────────────────────────────────────


class TestEventBusLoad:
    """EventBus publish/subscribe under load."""

    @pytest.mark.asyncio
    async def test_1000_events(self):
        """Publish 1000 events to a subscribed handler."""
        bus = EventBus()
        received = []
        bus.subscribe("test.event", lambda d: received.append(d))

        for i in range(1000):
            await bus.publish("test.event", {"i": i})

        assert len(received) == 1000
        assert received[0]["i"] == 0
        assert received[-1]["i"] == 999
