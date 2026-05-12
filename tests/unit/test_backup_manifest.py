"""Tests for monitor/app/backup/manifest.py."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.backup import manifest


def test_build_payload_shape(monkeypatch):
    monkeypatch.setenv("FASTTAK_VERSION", "v0.26.0")
    monkeypatch.setenv("FASTTAK_COMMIT", "abc1234")

    created = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    payload = manifest.build(
        created_at=created,
        hostname="example.test",
        postgres_versions={"cot": "15.5", "lldap": "16.2", "nodered": "16.2", "fastak": "16.2"},
    )

    assert payload["schema_version"] == 1
    assert payload["fasttak_version"] == "v0.26.0"
    assert payload["git_commit"] == "abc1234"
    assert payload["created_at"] == "2026-05-11T12:00:00+00:00"
    assert payload["producer_hostname"] == "example.test"
    assert payload["postgres_versions"] == {
        "cot": "15.5",
        "lldap": "16.2",
        "nodered": "16.2",
        "fastak": "16.2",
    }
    assert payload["components"] == [
        "postgres/cot.sql",
        "postgres/lldap.sql",
        "postgres/nodered.sql",
        "postgres/fastak.sql",
        "tak-certs.tar",
        "tak-config.tar",
        "nodered-data.tar",
        "env",
    ]


def test_collect_postgres_versions_returns_dict():
    """`collect_postgres_versions` shells out to psql — mock it."""
    fake_query = MagicMock(side_effect=lambda dsn: f"{dsn['dbname']}-15.x")
    versions = manifest.collect_postgres_versions(
        databases=[
            {"dbname": "cot", "host": "tak-database", "user": "u", "password": "p", "port": 5432},
            {"dbname": "lldap", "host": "app-db", "user": "u", "password": "p", "port": 5432},
        ],
        query=fake_query,
    )
    assert versions == {"cot": "cot-15.x", "lldap": "lldap-15.x"}


def test_query_version_decodes_bytes_from_sql_ascii(monkeypatch):
    """SHOW server_version returns bytes from SQL_ASCII DBs; must normalise to str."""
    fake_row = (b"15.1 (Debian 15.1-1.pgdg110+1)",)

    class _FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, _sql):
            pass

        def fetchone(self):
            return fake_row

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _FakeCursor()

    monkeypatch.setattr(manifest.psycopg, "connect", lambda **_: _FakeConn())
    result = manifest._query_version(
        {"dbname": "cot", "host": "h", "user": "u", "password": "p", "port": 5432}
    )
    assert isinstance(result, str)
    assert result.startswith("15.1")


def test_collect_postgres_versions_records_error(monkeypatch):
    def boom(_dsn):
        raise RuntimeError("connection refused")

    versions = manifest.collect_postgres_versions(
        databases=[
            {"dbname": "cot", "host": "tak-database", "user": "u", "password": "p", "port": 5432},
        ],
        query=boom,
    )
    # Only the exception type goes into the manifest — no message, no DSN.
    assert versions == {"cot": "unknown (RuntimeError)"}
