"""Builds the MANIFEST.json placed inside every backup tarball.

A manifest describes:
- which FastTAK version and git commit produced the backup,
- the Postgres server version of each database at backup time (so a
  restore can warn on version skew),
- the components present in the archive (so a partial backup fails loudly
  if a future v2 introduces selective backups).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import psycopg

from app.backup.config import fasttak_commit, fasttak_version

log = logging.getLogger(__name__)

COMPONENTS = [
    "postgres/cot.sql",
    "postgres/lldap.sql",
    "postgres/nodered.sql",
    "postgres/fastak.sql",
    "tak-certs.tar",
    "tak-config.tar",
    "nodered-data.tar",
    "env",
]


def build(
    *,
    created_at: datetime,
    hostname: str,
    postgres_versions: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "fasttak_version": fasttak_version(),
        "git_commit": fasttak_commit(),
        "created_at": created_at.isoformat(),
        "producer_hostname": hostname,
        "postgres_versions": postgres_versions,
        "components": list(COMPONENTS),
    }


def _query_version(dsn: dict[str, Any]) -> str:
    """Return the Postgres server version string. Real implementation.

    `SHOW server_version` returns bytes from SQL_ASCII-encoded databases
    (e.g. the TAK Server `cot` DB) and str from UTF-8 ones. Normalise to
    str so the result is JSON-serialisable for the manifest.
    """
    with psycopg.connect(**dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW server_version")
            row = cur.fetchone()
    if not row:
        return "unknown"
    value = row[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value


def collect_postgres_versions(
    *,
    databases: list[dict[str, Any]],
    query: Callable[[dict[str, Any]], str] = _query_version,
) -> dict[str, str]:
    """Probe each database for its server version.

    `databases` is a list of psycopg DSN dicts (dbname/host/user/password/port).
    `query` is injected so tests can stub psycopg.
    """
    versions: dict[str, str] = {}
    for db in databases:
        name = db["dbname"]
        try:
            versions[name] = query(db)
        except Exception as exc:
            # Log the full exception (server logs only); store just the
            # exception type in the manifest. Some psycopg error messages
            # echo the DSN, which would put the DB password into the
            # encrypted archive (and into anything that reads the manifest
            # post-decrypt).
            log.warning("Could not read Postgres version for %s: %s", name, exc)
            versions[name] = f"unknown ({type(exc).__name__})"
    return versions
