"""Simple async pub/sub event bus for decoupling components."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine


class EventBus:
    """Lightweight async event bus.

    Usage:
        bus = EventBus()
        bus.subscribe("trade.placed", my_handler)
        await bus.publish("trade.placed", trade_data)
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Coroutine]]] = defaultdict(list)

    def subscribe(self, event: str, handler: Callable[..., Coroutine]) -> None:
        self._handlers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable[..., Coroutine]) -> None:
        self._handlers[event] = [h for h in self._handlers[event] if h is not handler]

    async def publish(self, event: str, data: Any = None) -> None:
        for handler in self._handlers.get(event, []):
            try:
                await handler(data)
            except Exception as e:
                print(f"[EventBus] Error in handler for '{event}': {e}")
