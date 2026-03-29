"""Direct PostgreSQL connection for health checks and maintenance.

Replaces docker exec for database operations (DD-018 portability).
Uses psycopg (sync) — scheduler jobs run in threads, not async.
No connection pool — queries are infrequent (hourly health checks,
occasional manual VACUUM).
"""

import logging
from urllib.parse import quote_plus

import psycopg

from app.config import settings

log = logging.getLogger(__name__)


def _build_dsn() -> str:
    """Build a PostgreSQL connection string."""
    if settings.tak_db_url:
        return settings.tak_db_url
    if not settings.tak_db_password:
        raise ValueError(
            "TAK_DB_PASSWORD must be set (or provide TAK_DB_URL for custom connections)"
        )
    password = quote_plus(settings.tak_db_password)
    return f"postgresql://martiuser:{password}@tak-database:5432/cot"


def query(sql: str) -> list[tuple]:
    """Execute a SQL query and return all rows."""
    dsn = _build_dsn()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def execute(sql: str) -> None:
    """Execute a SQL statement with no return value (e.g., VACUUM)."""
    dsn = _build_dsn()
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
