"""Tests for app.api.health.config_drift — .env change detection."""

from app.api.health import config_drift


class TestConfigDrift:
    def _reset(self):
        config_drift._startup_hash = None

    def test_init_captures_baseline(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / ".env"
        env_file.write_text("SERVER_ADDRESS=test.example.com")
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        assert config_drift._startup_hash is not None

    def test_unchanged_returns_changed_false(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / ".env"
        env_file.write_text("SERVER_ADDRESS=test.example.com")
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        result = config_drift.check_config_drift()
        assert result == {"changed": False}

    def test_changed_returns_changed_true(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / ".env"
        env_file.write_text("SERVER_ADDRESS=test.example.com")
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        env_file.write_text("SERVER_ADDRESS=new.example.com")
        result = config_drift.check_config_drift()
        assert result["changed"] is True
        assert "message" in result

    def test_missing_file_returns_not_mounted(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / "nonexistent"
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        result = config_drift.check_config_drift()
        assert result["changed"] is False
        assert ".env not mounted" in result["message"]

    def test_file_becomes_unreadable(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / ".env"
        env_file.write_text("SERVER_ADDRESS=test.example.com")
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        env_file.unlink()
        result = config_drift.check_config_drift()
        assert "error" in result
