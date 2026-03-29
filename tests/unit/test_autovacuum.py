"""Tests for app.api.health.autovacuum — autovacuum health checks."""

from unittest.mock import patch


class TestGetAutovacuumHealth:
    @patch("app.api.health.autovacuum.query")
    def test_returns_items(self, mock_query):
        mock_query.return_value = [
            ("cot_router", 100, 100000, 0.1),
        ]
        from app.api.health.autovacuum import get_autovacuum_health

        result = get_autovacuum_health()
        assert "items" in result
        assert "status" not in result
        assert result["items"][0]["table"] == "cot_router"
        assert result["items"][0]["dead_pct"] == 0.1

    @patch("app.api.health.autovacuum.query")
    def test_empty_tables(self, mock_query):
        mock_query.return_value = []
        from app.api.health.autovacuum import get_autovacuum_health

        result = get_autovacuum_health()
        assert result["items"] == []

    @patch("app.api.health.autovacuum.query")
    def test_bytes_table_name_decoded(self, mock_query):
        """psycopg may return relname as bytes — should be decoded to str."""
        mock_query.return_value = [
            (b"cot_router", 50, 100000, 0.0),
        ]
        from app.api.health.autovacuum import get_autovacuum_health

        result = get_autovacuum_health()
        assert result["items"][0]["table"] == "cot_router"

    @patch("app.api.health.autovacuum.query")
    def test_multiple_tables_returned(self, mock_query):
        mock_query.return_value = [
            ("cot_router", 20000, 100000, 16.7),
            ("mission", 4, 2, 66.7),
        ]
        from app.api.health.autovacuum import get_autovacuum_health

        result = get_autovacuum_health()
        assert len(result["items"]) == 2
        assert result["items"][1]["dead_pct"] == 66.7

    @patch("app.api.health.autovacuum.query", side_effect=Exception("connection refused"))
    def test_connection_error(self, mock_query):
        from app.api.health.autovacuum import get_autovacuum_health

        result = get_autovacuum_health()
        assert "error" in result
