"""Tests for app.api.ops.database — VACUUM operations."""

from unittest.mock import patch


class TestVacuumDatabase:
    @patch("app.api.ops.database.execute")
    def test_success(self, mock_execute):
        from app.api.ops.database import vacuum_database

        result = vacuum_database()
        assert result["success"] is True
        assert result["command"] == "VACUUM FULL ANALYZE"
        mock_execute.assert_called_once_with("VACUUM FULL ANALYZE")

    @patch("app.api.ops.database.execute", side_effect=Exception("database locked"))
    def test_failure(self, mock_execute):
        from app.api.ops.database import vacuum_database

        result = vacuum_database()
        assert result["success"] is False
        assert "locked" in result["error"]
