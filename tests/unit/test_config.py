"""Tests for app.config — Settings loading from environment."""

from app.config import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.server_address == "localhost"
        assert s.deploy_mode == "direct"
        assert s.smtp_port == 587
        assert s.update_check_interval == 21600

    def test_loads_from_env(self, monkeypatch):
        monkeypatch.setenv("SERVER_ADDRESS", "tak.example.com")
        monkeypatch.setenv("DEPLOY_MODE", "subdomain")
        monkeypatch.setenv("SMTP_PORT", "465")
        monkeypatch.setenv("TAK_DB_PASSWORD", "secret123")
        s = Settings()
        assert s.server_address == "tak.example.com"
        assert s.deploy_mode == "subdomain"
        assert s.smtp_port == 465
        assert s.tak_db_password == "secret123"

    def test_extra_vars_ignored(self, monkeypatch):
        monkeypatch.setenv("TOTALLY_UNKNOWN_VAR", "whatever")
        s = Settings()  # should not raise
        assert s.server_address == "localhost"

    def test_int_coercion(self, monkeypatch):
        monkeypatch.setenv("UPDATE_CHECK_INTERVAL", "3600")
        s = Settings()
        assert s.update_check_interval == 3600
        assert isinstance(s.update_check_interval, int)

    def test_user_management_defaults(self):
        s = Settings()
        assert s.authentik_url == "http://authentik-server:9000"
        assert s.authentik_api_token == ""
        assert s.tak_server_url == "https://tak-server:8443"
        assert s.tak_api_cert_path == ""
        assert s.tak_api_cert_password == "atakatak"
        assert s.users_hidden_prefixes == "ak-,adm_,svc_,ma-"
        assert s.user_expiry_check_interval == 60
        assert s.enrollment_ttl_minutes == 15
        assert s.tak_enrollment_port == 8446
