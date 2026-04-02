"""Security middleware for FastAPI — rate limiting, body size, headers, audit.

App-level defense-in-depth (backup for when Nginx is bypassed in dev).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from bot.config import settings
from bot.monitoring.metrics import EXECUTION_CHECKS_FAILED

logger = logging.getLogger(__name__)


# ── Rate Limiter (in-memory, per-IP) ────────────────────

class _TokenBucket:
    """Simple token bucket rate limiter."""

    def __init__(self, rate: float, capacity: float):
        self._rate = rate        # tokens per second
        self._capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


_buckets: dict[str, _TokenBucket] = defaultdict(
    lambda: _TokenBucket(
        rate=settings.security.rate_limit_rpm / 60.0,
        capacity=settings.security.rate_limit_rpm / 60.0 * 10,  # 10s burst
    )
)

# Paths exempt from rate limiting
_EXEMPT_PATHS = frozenset({"/metrics", "/health", "/api/overview"})


class SecurityMiddleware(BaseHTTPMiddleware):
    """Combined security middleware: rate limit, body size, headers, audit."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"

        # 1. Request body size limit (skip GET/HEAD/OPTIONS)
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > settings.security.max_body_size:
                logger.warning(
                    "Request too large from %s: %s bytes on %s",
                    client_ip, content_length, path,
                )
                return JSONResponse(
                    {"detail": "Request body too large"},
                    status_code=413,
                )

        # 2. Rate limiting (skip exempt paths)
        if path not in _EXEMPT_PATHS:
            bucket = _buckets[client_ip]
            if not bucket.allow():
                logger.warning("Rate limit hit for %s on %s", client_ip, path)
                return JSONResponse(
                    {"detail": "Rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": "5"},
                )

        # 3. Process request
        t0 = time.monotonic()
        response = await call_next(request)
        latency_ms = (time.monotonic() - t0) * 1000

        # 4. Security headers (defense-in-depth, duplicates nginx for dev)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"

        # 5. Audit log (structured)
        status = response.status_code
        if status >= 400 or path.startswith("/api/v2/"):
            logger.info(
                "audit: %s %s %s → %d (%.0fms)",
                client_ip, request.method, path, status, latency_ms,
            )

        return response
