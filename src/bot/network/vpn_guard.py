"""VPN Guard — block orders when no VPN tunnel is detected.

Usage:
    from bot.network.vpn_guard import is_vpn_active

    if not await is_vpn_active():
        # skip order placement
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import subprocess
import time

logger = logging.getLogger(__name__)

# Cached result: (is_active, timestamp)
_cache: tuple[bool, float] = (False, 0.0)
_CACHE_TTL = 30  # seconds


def _has_vpn_interface() -> bool:
    """Check for common VPN/tunnel network interfaces."""
    vpn_prefixes = ("tun", "utun", "wg", "ppp", "tap", "tailscale", "proton", "nord")
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["ifconfig", "-l"],
                capture_output=True, text=True, timeout=5,
            )
            ifaces = result.stdout.lower().split()
        else:
            # Linux
            result = subprocess.run(
                ["ip", "-o", "link", "show"],
                capture_output=True, text=True, timeout=5,
            )
            ifaces = [
                line.split(":")[1].strip().lower()
                for line in result.stdout.splitlines()
                if ":" in line and len(line.split(":")) > 1
            ]
        return any(
            iface.startswith(prefix)
            for iface in ifaces
            for prefix in vpn_prefixes
        )
    except Exception as exc:
        logger.warning("VPN interface check failed: %s", exc)
        return False


async def is_vpn_active() -> bool:
    """Check if a VPN is active. Result is cached for 30s.

    Set VPN_CHECK=disabled in .env to skip the check entirely.

    Returns:
        True if VPN detected (or check disabled), False otherwise.
    """
    global _cache
    now = time.time()

    if now - _cache[1] < _CACHE_TTL:
        return _cache[0]

    # Allow disabling the check via env var
    if os.environ.get("VPN_CHECK", "auto").lower() == "disabled":
        _cache = (True, now)
        return True

    result = await asyncio.to_thread(_has_vpn_interface)
    _cache = (result, now)

    if not result:
        logger.warning("[VPN_GUARD] No VPN interface detected — orders will be blocked")

    return result
