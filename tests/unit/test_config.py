"""Tests for app.config — Settings loading from environment."""

from app.config import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.fqdn == "localhost"
        assert s.smtp_port == 587
        assert s.health_check_interval == 60
        assert s.cert_warn_days == 30

    def test_loads_from_env(self, monkeypatch):
        monkeypatch.setenv("FQDN", "tak.example.com")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("TAK_DB_PASSWORD", "secret123")
        s = Settings()
        assert s.fqdn == "tak.example.com"
        assert s.smtp_port == 465
        assert s.tak_db_password == "secret123"

    def test_extra_vars_ignored(self, monkeypatch):
        monkeypatch.setenv("TOTALLY_UNKNOWN_VAR", "whatever")
        s = Settings()  # should not raise
        assert s.fqdn == "localhost"

    def test_int_coercion(self, monkeypatch):
        monkeypatch.setenv("HEALTH_CHECK_INTERVAL", "120")
        s = Settings()
        assert s.health_check_interval == 120
        assert isinstance(s.health_check_interval, int)
