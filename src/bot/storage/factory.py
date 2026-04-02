"""Database adapter factory.

Selects the appropriate async database backend (SQLite or PostgreSQL)
based on the ``db_url`` setting in ``config.Settings``.
"""
from __future__ import annotations

import logging

from bot.config import settings
from bot.storage.database import Database
from bot.storage.postgres import PostgresDatabase

log = logging.getLogger(__name__)


async def create_database() -> Database | PostgresDatabase:
    """Create and connect the appropriate database adapter.

    If ``settings.db_url`` is set and starts with ``"postgresql"``, a
    ``PostgresDatabase`` backed by an ``asyncpg`` connection pool is
    returned.  Otherwise the default ``Database`` (async SQLite via
    ``aiosqlite``) is used with ``settings.db_path``.

    Returns:
        A connected database instance ready for queries.
    """
    if settings.db_url and settings.db_url.startswith("postgresql"):
        log.info("Using PostgreSQL backend: %s", _mask_dsn(settings.db_url))
        db = PostgresDatabase(settings.db_url)
        await db.connect()
        return db

    log.info("Using SQLite backend: %s", settings.db_path)
    db_sqlite = Database(settings.db_path)
    await db_sqlite.connect()
    return db_sqlite


def _mask_dsn(dsn: str) -> str:
    """Redact password from a DSN for safe logging.

    Args:
        dsn: A PostgreSQL connection string.

    Returns:
        The DSN with any password replaced by ``***``.
    """
    try:
        # Format: postgresql://user:password@host:port/dbname
        if "@" in dsn and ":" in dsn.split("@")[0]:
            scheme_user, rest = dsn.split("@", 1)
            # scheme_user = "postgresql://user:password"
            parts = scheme_user.rsplit(":", 1)
            if len(parts) == 2:
                return f"{parts[0]}:***@{rest}"
    except Exception:
        pass
    return dsn
