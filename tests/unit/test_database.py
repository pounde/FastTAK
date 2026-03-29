"""Tests for app.api.health.database — CoT DB size queries."""

from unittest.mock import patch


class TestGetCotDbSize:
    @patch("app.api.health.database.query")
    def test_returns_size(self, mock_query):
        mock_query.side_effect = [
            [(1073741824,)],
            [(536870912,)],
        ]

        from app.api.health.database import get_cot_db_size

        result = get_cot_db_size()
        assert result["size_bytes"] == 1073741824
        assert result["live_bytes"] == 536870912
        assert "GB" in result["size_human"] or "MB" in result["size_human"]
        assert "live_bytes" in result
        assert "live_human" in result
        assert "status" not in result

    @patch("app.api.health.database.query", side_effect=Exception("connection refused"))
    def test_connection_error(self, mock_query):
        from app.api.health.database import get_cot_db_size

        result = get_cot_db_size()
        assert "error" in result


class TestHumanSize:
    def test_bytes(self):
        from app.api.health.database import _human_size

        assert _human_size(500) == "500.0 B"

    def test_gigabytes(self):
        from app.api.health.database import _human_size

        assert "GB" in _human_size(5 * 1024 * 1024 * 1024)

    def test_terabytes(self):
        from app.api.health.database import _human_size

        assert "TB" in _human_size(2 * 1024 * 1024 * 1024 * 1024)
