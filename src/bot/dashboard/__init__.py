"""Dashboard package for Polymarket Bot v2."""

from .app import create_app
from .log_buffer import LogBuffer

__all__ = ["create_app", "LogBuffer"]
