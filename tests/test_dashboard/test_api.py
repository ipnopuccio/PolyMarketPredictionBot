"""Tests for the FastAPI dashboard and authenticated API endpoints."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from bot.dashboard.app import create_app
from bot.storage.database import Database
from tests.conftest import insert_resolved_trades


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

API_KEY = "test-key-12345"


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    """Ensure API_KEY env var is set before the app loads auth."""
    monkeypatch.setenv("API_KEY", API_KEY)
    # Reset the cached key in auth module
    import bot.dashboard.auth as auth_mod
    auth_mod._api_key = None


@pytest.fixture
async def client(db: Database) -> AsyncClient:
    """HTTPX async client wired to the FastAPI ASGI app."""
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


# =========================================================================
# HTML dashboard (no auth)
# =========================================================================

class TestDashboardHtml:
    async def test_root_returns_html(self, client: AsyncClient):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Polymarket Bot v2" in resp.text


# =========================================================================
# Public API: /api/overview
# =========================================================================

class TestOverview:
    async def test_overview_returns_aggregated_data(self, client: AsyncClient):
        resp = await client.get("/api/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_pnl" in data
        assert "total_bankroll" in data
        assert "win_rate" in data
        assert "total_trades" in data
        assert "mode" in data


# =========================================================================
# Auth required on /api/v2/*
# =========================================================================

class TestAuth:
    async def test_v2_without_key_returns_403(self, client: AsyncClient):
        resp = await client.get("/api/v2/positions")
        assert resp.status_code == 403

    async def test_v2_with_wrong_key_returns_403(self, client: AsyncClient):
        resp = await client.get(
            "/api/v2/positions",
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 403

    async def test_v2_with_correct_key_passes(self, client: AsyncClient):
        resp = await client.get("/api/v2/positions", headers=_auth_headers())
        assert resp.status_code == 200


# =========================================================================
# GET /api/v2/positions
# =========================================================================

class TestPositions:
    async def test_positions_empty(self, client: AsyncClient):
        resp = await client.get("/api/v2/positions", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_positions_shows_open_trades(
        self, client: AsyncClient, db: Database,
    ):
        """Insert an open trade for a winning bot, confirm it appears."""
        import time
        from bot.core.types import FeedSnapshot
        snap = FeedSnapshot(connected=True, last_update=time.time())
        tid = await db.reserve_and_insert_trade(
            strategy="MOMENTUM", asset="BTC", market_id="mkt_pos",
            signal="BUY_YES", entry_price=0.50, bet_size=2.0,
            confidence=0.7, regime="TRENDING",
            snapshot=snap.to_dict(), rsi=45.0, bb_pct=0.5,
        )
        assert tid is not None

        resp = await client.get("/api/v2/positions", headers=_auth_headers())
        assert resp.status_code == 200
        positions = resp.json()
        assert len(positions) >= 1
        assert any(p["market_id"] == "mkt_pos" for p in positions)


# =========================================================================
# GET /api/v2/pnl
# =========================================================================

class TestPnl:
    async def test_pnl_structure(self, client: AsyncClient):
        resp = await client.get("/api/v2/pnl", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert "total_realized_pnl" in data
        assert "total_unrealized_cost" in data
        assert "bots" in data
        assert isinstance(data["bots"], list)


# =========================================================================
# GET /api/v2/params
# =========================================================================

class TestParams:
    async def test_params_returns_config(self, client: AsyncClient):
        resp = await client.get("/api/v2/params", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert "momentum" in data
        assert "risk" in data
        assert "sizer" in data


# =========================================================================
# GET /api/v2/health
# =========================================================================

class TestHealth:
    async def test_health_returns_components(self, client: AsyncClient):
        with patch(
            "bot.dashboard.server.is_vpn_active",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = await client.get("/api/v2/health", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "components" in data
        assert "database" in data["components"]
        assert "vpn" in data["components"]
        assert "feeds" in data["components"]
