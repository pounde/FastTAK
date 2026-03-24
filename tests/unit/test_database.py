"""Tests for app.api.health.database — CoT DB size queries."""

from unittest.mock import MagicMock, patch


class TestGetCotDbSize:
    @patch("app.api.health.database.find_container")
    def test_returns_size(self, mock_find, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.database.settings", mock_settings)
        container = MagicMock()
        container.exec_run.return_value = (0, b"1073741824")
        mock_find.return_value = container

        from app.api.health.database import get_cot_db_size

        result = get_cot_db_size()
        assert result["size_bytes"] == 1073741824
        assert result["status"] == "ok"
        assert "GB" in result["size_human"] or "MB" in result["size_human"]

    @patch("app.api.health.database.find_container", return_value=None)
    def test_container_not_found(self, mock_find):
        from app.api.health.database import get_cot_db_size

        result = get_cot_db_size()
        assert "error" in result

    @patch("app.api.health.database.find_container")
    def test_warning_threshold(self, mock_find, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.database.settings", mock_settings)
        container = MagicMock()
        container.exec_run.return_value = (0, b"30000000000")
        mock_find.return_value = container

        from app.api.health.database import get_cot_db_size

        result = get_cot_db_size()
        assert result["status"] == "warning"


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
