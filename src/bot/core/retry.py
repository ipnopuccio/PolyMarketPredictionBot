"""Retry utilities with exponential backoff for async functions.

Usage::

    from bot.core.retry import with_retry

    @with_retry(max_attempts=3)
    async def fetch_something():
        ...

    @with_retry(max_attempts=5, base_delay=1.0, max_delay=60.0)
    async def fragile_api_call():
        ...
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

# Transient network errors that are safe to retry
RETRIABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    ConnectionError,
    OSError,
)


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retriable: tuple[type[Exception], ...] = RETRIABLE_EXCEPTIONS,
) -> Callable:
    """Decorator for async functions with exponential backoff retry.

    Args:
        max_attempts: Total attempts (1 = no retry, 3 = up to 2 retries).
        base_delay: Initial delay in seconds, doubles each attempt.
        max_delay: Maximum delay cap in seconds.
        retriable: Exception types that trigger a retry.

    HTTP 429 (rate-limited) responses are always retried, honouring the
    ``Retry-After`` header when present.  Other 4xx/5xx ``HTTPStatusError``
    exceptions are *not* retried (they are re-raised immediately).
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)

                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429:
                        # Respect Retry-After or fall back to exponential delay
                        retry_after = exc.response.headers.get("Retry-After")
                        if retry_after is not None:
                            wait = min(float(retry_after), max_delay)
                        else:
                            wait = min(base_delay * (2 ** (attempt - 1)), max_delay)
                        logger.warning(
                            "[retry] %s rate-limited (429), waiting %.1fs "
                            "(attempt %d/%d)",
                            fn.__name__, wait, attempt, max_attempts,
                        )
                        if attempt < max_attempts:
                            await asyncio.sleep(wait)
                        last_exc = exc
                    else:
                        raise  # non-retriable HTTP error — propagate immediately

                except retriable as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    wait = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "[retry] %s failed: %s — retrying in %.1fs "
                        "(attempt %d/%d)",
                        fn.__name__, exc, wait, attempt, max_attempts,
                    )
                    await asyncio.sleep(wait)

            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
