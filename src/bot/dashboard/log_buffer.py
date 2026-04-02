"""Ring-buffer logging handler for the /logs API endpoint."""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone


class LogBuffer(logging.Handler):
    """Stores the last *capacity* log entries in a thread-safe deque."""

    _instance: LogBuffer | None = None

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self._buffer: deque[dict] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        self._buffer.append({
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "message": self.format(record),
            "logger": record.name,
        })

    def get_entries(self, n: int = 100, level: str | None = None) -> list[dict]:
        """Return the last *n* entries, optionally filtered by level."""
        entries = list(self._buffer)
        if level:
            entries = [e for e in entries if e["level"] == level.upper()]
        return entries[-n:]

    @classmethod
    def install(cls, capacity: int = 500) -> LogBuffer:
        """Install the handler on the root logger (singleton)."""
        if cls._instance is None:
            cls._instance = cls(capacity)
            cls._instance.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
            logging.getLogger().addHandler(cls._instance)
        return cls._instance

    @classmethod
    def get(cls) -> LogBuffer | None:
        """Return the installed instance, or None."""
        return cls._instance
