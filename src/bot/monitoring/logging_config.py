"""Structured JSON logging configuration.

Replaces plain-text logging with JSON-formatted output suitable for
log aggregation (ELK, Loki, CloudWatch, etc.).

Usage:
    from bot.monitoring.logging_config import setup_logging
    setup_logging()  # call once at startup
"""
from __future__ import annotations

import logging
import os
import sys

from pythonjsonlogger.json import JsonFormatter


class BotJsonFormatter(JsonFormatter):
    """JSON formatter with bot-specific defaults."""

    def __init__(self, **kwargs):
        fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
        super().__init__(fmt=fmt, **kwargs)

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        if record.exc_info and not log_record.get("exc_info"):
            log_record["exc_info"] = self.formatException(record.exc_info)


def setup_logging(level: str | None = None, json_output: bool | None = None) -> None:
    """Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to LOG_LEVEL env var or INFO.
        json_output: If True, use JSON format. If None, auto-detect:
                     JSON when LOG_FORMAT=json or running in Docker.
    """
    log_level = level or os.environ.get("LOG_LEVEL", "INFO")
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Auto-detect JSON mode: explicit env var, or Docker environment
    if json_output is None:
        env_format = os.environ.get("LOG_FORMAT", "").lower()
        in_docker = os.path.exists("/.dockerenv")
        json_output = env_format == "json" or in_docker

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)

    if json_output:
        handler.setFormatter(BotJsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        ))

    root.addHandler(handler)

    # Quiet noisy libraries
    for noisy in ("httpx", "httpcore", "ccxt", "websockets", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
