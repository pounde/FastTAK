"""Direct PostgreSQL connection for the fastak audit/events database.

Mirrors app.db (which targets TAK Server's cot DB) but connects to app-db
instead. No connection pool — events writes are infrequent compared with
TAK CoT throughput. If profiling shows pressure, introduce a shared
psycopg_pool.ConnectionPool here and reuse it across audit/events code.
"""

import logging
from contextlib import contextmanager
from urllib.parse import quote_plus

import psycopg
from psycopg.rows import dict_row

from app.config import settings

log = logging.getLogger(__name__)


def _build_dsn() -> str:
    """Build a PostgreSQL connection string for the fastak DB."""
    if settings.fastak_db_url:
        return settings.fastak_db_url
    if not settings.fastak_db_password:
        raise ValueError("FASTAK_DB_PASSWORD must be set (or provide FASTAK_DB_URL)")
    password = quote_plus(settings.fastak_db_password)
    user = settings.app_db_user
    host = settings.app_db_host
    return f"postgresql://{user}:{password}@{host}:5432/fastak"


@contextmanager
def connection():
    """Yield a psycopg connection in autocommit mode with dict-row cursor."""
    dsn = _build_dsn()
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        yield conn


def execute(sql: str, params: tuple | None = None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())


def fetch(sql: str, params: tuple | None = None) -> list[dict]:
    """Execute a SELECT and return rows as dicts (via dict_row).

    Distinct from `app.db.query`, which returns rows as tuples.
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        return list(cur.fetchall())
