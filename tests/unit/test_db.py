"""Tests for app.db — database client module."""

from unittest.mock import MagicMock, patch


class TestBuildDsn:
    def test_uses_explicit_url(self, mock_settings, monkeypatch):
        mock_settings.tak_db_url = "postgresql://custom:pass@host:5432/mydb"
        monkeypatch.setattr("app.db.settings", mock_settings)
        from app.db import _build_dsn

        assert _build_dsn() == "postgresql://custom:pass@host:5432/mydb"

    def test_constructs_from_password(self, mock_settings, monkeypatch):
        mock_settings.tak_db_url = ""
        mock_settings.tak_db_password = "secret"
        monkeypatch.setattr("app.db.settings", mock_settings)
        from app.db import _build_dsn

        dsn = _build_dsn()
        assert "secret" in dsn
        assert "tak-database" in dsn
        assert "cot" in dsn

    def test_empty_password_raises(self, mock_settings, monkeypatch):
        mock_settings.tak_db_url = ""
        mock_settings.tak_db_password = ""
        monkeypatch.setattr("app.db.settings", mock_settings)
        import pytest
        from app.db import _build_dsn

        with pytest.raises(ValueError, match="TAK_DB_PASSWORD"):
            _build_dsn()


class TestQuery:
    @patch("app.db.psycopg.connect")
    def test_returns_rows(self, mock_connect, mock_settings, monkeypatch):
        mock_settings.tak_db_url = "postgresql://test:test@localhost/test"
        monkeypatch.setattr("app.db.settings", mock_settings)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("cot_router", 100, 1000)]
        mock_cursor.description = [("relname",), ("dead",), ("live",)]
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_connect.return_value = mock_conn

        from app.db import query

        rows = query("SELECT 1")
        assert len(rows) == 1

    @patch("app.db.psycopg.connect")
    def test_execute_no_fetch(self, mock_connect, mock_settings, monkeypatch):
        mock_settings.tak_db_url = "postgresql://test:test@localhost/test"
        monkeypatch.setattr("app.db.settings", mock_settings)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_connect.return_value = mock_conn

        from app.db import execute

        execute("VACUUM FULL ANALYZE")
        mock_cursor.execute.assert_called_once_with("VACUUM FULL ANALYZE", ())
        mock_connect.assert_called_once_with(
            "postgresql://test:test@localhost/test", autocommit=True
        )


def test_query_forwards_params_to_cursor(monkeypatch):
    from unittest.mock import MagicMock

    import app.db as db

    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [("a",)]
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    monkeypatch.setattr(db.psycopg, "connect", lambda *a, **kw: fake_conn)
    monkeypatch.setattr(db.settings, "tak_db_url", "postgresql://x:y@h/cot")

    rows = db.query("SELECT %s", ("a",))
    assert rows == [("a",)]
    fake_cursor.execute.assert_called_once_with("SELECT %s", ("a",))


def test_query_no_params_still_works(monkeypatch):
    from unittest.mock import MagicMock

    import app.db as db

    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = []
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    monkeypatch.setattr(db.psycopg, "connect", lambda *a, **kw: fake_conn)
    monkeypatch.setattr(db.settings, "tak_db_url", "postgresql://x:y@h/cot")

    db.query("SELECT 1")
    fake_cursor.execute.assert_called_once_with("SELECT 1", ())
