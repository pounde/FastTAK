"""Tests for app.monitoring_config — YAML threshold config loader."""


class TestLoadConfig:
    def test_loads_defaults(self):
        from app.monitoring_config import load_config

        config = load_config()
        assert "database" in config
        assert config["database"]["interval"] == 60
        assert config["database"]["thresholds"]["size_bytes"]["warning"] == 25000000000

    def test_all_services_present(self):
        from app.monitoring_config import load_config

        config = load_config()
        expected = {
            "database",
            "autovacuum",
            "disk",
            "certs",
            "tls",
            "containers",
            "config",
            "updates",
        }
        assert set(config.keys()) == expected

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("FASTAK_MON_DATABASE__INTERVAL", "30")
        from app.monitoring_config import load_config

        config = load_config()
        assert config["database"]["interval"] == 30

    def test_boolean_thresholds(self):
        from app.monitoring_config import load_config

        config = load_config()
        assert config["config"]["thresholds"]["changed"]["true"] == "note"
        assert config["config"]["thresholds"]["changed"]["false"] == "ok"
