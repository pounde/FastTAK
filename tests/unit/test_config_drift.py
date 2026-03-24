"""Tests for app.api.health.config_drift — .env change detection."""

from app.api.health import config_drift


class TestConfigDrift:
    def _reset(self):
        config_drift._startup_hash = None

    def test_init_captures_baseline(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / ".env"
        env_file.write_text("FQDN=test.example.com")
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        assert config_drift._startup_hash is not None

    def test_unchanged_returns_ok(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / ".env"
        env_file.write_text("FQDN=test.example.com")
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        result = config_drift.check_config_drift()
        assert result["status"] == "ok"

    def test_changed_returns_changed(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / ".env"
        env_file.write_text("FQDN=test.example.com")
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        env_file.write_text("FQDN=new.example.com")
        result = config_drift.check_config_drift()
        assert result["status"] == "changed"

    def test_missing_file_returns_unavailable(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / "nonexistent"
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        result = config_drift.check_config_drift()
        assert result["status"] == "unavailable"

    def test_file_becomes_unreadable(self, tmp_path, monkeypatch):
        self._reset()
        env_file = tmp_path / ".env"
        env_file.write_text("FQDN=test.example.com")
        monkeypatch.setattr(config_drift, "ENV_FILE", env_file)

        config_drift.init_config_hash()
        env_file.unlink()
        result = config_drift.check_config_drift()
        assert result["status"] == "error"
