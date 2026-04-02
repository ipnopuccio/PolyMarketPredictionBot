"""API key authentication for dashboard API endpoints.

Supports dual API keys for zero-downtime rotation:
  - API_KEY (primary) — always active
  - API_KEY_SECONDARY (secondary) — optional, for rotation window

Rotation flow:
  1. Set API_KEY_SECONDARY=<new_key> in .env
  2. Restart bot (or hot-reload config)
  3. Update all clients to use <new_key>
  4. Move <new_key> to API_KEY, clear API_KEY_SECONDARY
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_api_key: str | None = None


def get_api_key() -> str:
    """Load API_KEY from env, or generate a new one and persist to .env."""
    global _api_key
    if _api_key:
        return _api_key

    key = os.environ.get("API_KEY")
    if key:
        _api_key = key
        return key

    # Generate new key
    key = secrets.token_urlsafe(32)
    _api_key = key
    os.environ["API_KEY"] = key

    # Persist to .env
    env_path = Path(__file__).resolve().parents[3] / ".env"
    try:
        with open(env_path, "a") as f:
            f.write(f"API_KEY={key}\n")
    except Exception as exc:
        logger.warning("Could not save API key to .env: %s", exc)

    # Print to console exactly once
    print(f"\n{'=' * 54}")
    print(f"  Dashboard API Key (save this!):")
    print(f"  {key}")
    print(f"{'=' * 54}\n")

    return key


def _get_valid_keys() -> set[str]:
    """Return all currently valid API keys (primary + secondary)."""
    keys = {get_api_key()}
    secondary = os.environ.get("API_KEY_SECONDARY", "")
    if secondary:
        keys.add(secondary)
    return keys


async def verify_api_key(key: str | None = Security(_api_key_header)) -> str:
    """FastAPI dependency — reject requests without a valid X-API-Key.

    Accepts both primary and secondary keys for rotation.
    """
    if not key or key not in _get_valid_keys():
        logger.warning("Auth failure: invalid API key attempt")
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return key
