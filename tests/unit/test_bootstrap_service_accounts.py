"""Tests for init-identity service account creation functions.

These test the ensure_svc_*_user() functions that create Authentik LDAP
users matching cert CNs for x509groups resolution.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# bootstrap.py lives outside the normal package structure — add to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "init-identity"))


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """Set required env vars so bootstrap module can import."""
    monkeypatch.setenv("AUTHENTIK_API_TOKEN", "test-token")
    monkeypatch.setenv("LDAP_BIND_PASSWORD", "test-password")


@pytest.fixture
def mock_api(monkeypatch):
    """Mock api_get and api_post in the bootstrap module."""
    import bootstrap

    get = MagicMock()
    post = MagicMock()
    monkeypatch.setattr(bootstrap, "api_get", get)
    monkeypatch.setattr(bootstrap, "api_post", post)
    return get, post


class TestEnsureSvcFasttakapiUser:
    """Tests for ensure_svc_fasttakapi_user().

    Admin access is granted via certmod -A in register-api-cert.sh, not LDAP groups.
    Bootstrap only creates the Authentik user — no group assignment.
    """

    def test_creates_user_when_not_found(self, mock_api):
        from bootstrap import ensure_svc_fasttakapi_user

        api_get, api_post = mock_api
        api_get.side_effect = [
            {"results": []},
        ]
        api_post.side_effect = [
            {"pk": 42, "username": "svc_fasttakapi"},
        ]

        ensure_svc_fasttakapi_user()

        assert api_post.call_count == 1
        create_call = api_post.call_args_list[0]
        assert create_call[0][0] == "core/users/"
        payload = create_call[0][1]
        assert payload["username"] == "svc_fasttakapi"
        assert payload["name"] == "FastTAK API Service Account"
        assert payload["type"] == "service_account"

    def test_skips_creation_when_user_exists(self, mock_api):
        from bootstrap import ensure_svc_fasttakapi_user

        api_get, api_post = mock_api
        api_get.side_effect = [
            {"results": [{"pk": 42, "username": "svc_fasttakapi"}]},
        ]

        ensure_svc_fasttakapi_user()

        api_post.assert_not_called()

    def test_does_not_assign_tak_role_admin(self, mock_api):
        """svc_fasttakapi gets admin via certmod -A, not LDAP group."""
        from bootstrap import ensure_svc_fasttakapi_user

        api_get, api_post = mock_api
        api_get.side_effect = [
            {"results": []},
        ]
        api_post.side_effect = [
            {"pk": 42, "username": "svc_fasttakapi"},
        ]

        ensure_svc_fasttakapi_user()

        for call in api_post.call_args_list:
            assert "tak_ROLE_ADMIN" not in str(call), "Should not assign tak_ROLE_ADMIN group"


class TestEnsureSvcNoderedUser:
    """Tests for ensure_svc_nodered_user().

    svc_nodered is a data service account. No groups are assigned at bootstrap —
    the admin assigns tak_* groups via the dashboard when building flows.
    """

    def test_creates_user_with_svc_prefix(self, mock_api):
        from bootstrap import ensure_svc_nodered_user

        api_get, api_post = mock_api
        api_get.side_effect = [
            {"results": []},
        ]
        api_post.side_effect = [
            {"pk": 43, "username": "svc_nodered"},
        ]

        ensure_svc_nodered_user()

        assert api_post.call_count == 1
        create_call = api_post.call_args_list[0]
        payload = create_call[0][1]
        assert payload["username"] == "svc_nodered"
        assert payload["name"] == "Node-RED Service Account"

    def test_does_not_assign_tak_role_admin(self, mock_api):
        """svc_nodered has no groups at bootstrap — admin assigns them later."""
        from bootstrap import ensure_svc_nodered_user

        api_get, api_post = mock_api
        api_get.side_effect = [
            {"results": []},
        ]
        api_post.side_effect = [
            {"pk": 43, "username": "svc_nodered"},
        ]

        ensure_svc_nodered_user()

        for call in api_post.call_args_list:
            assert "tak_ROLE_ADMIN" not in str(call), "Should not assign tak_ROLE_ADMIN group"


class TestHiddenPrefixes:
    """Verify USERS_HIDDEN_PREFIXES includes svc_ convention."""

    def test_settings_hide_service_accounts(self, mock_api, tmp_path, monkeypatch):
        import json

        import bootstrap

        monkeypatch.setattr(bootstrap, "TAK_DIR", str(tmp_path / "tak"))

        # Create the cert files that configure_tak_portal expects
        cert_dir = tmp_path / "tak" / "certs" / "files"
        cert_dir.mkdir(parents=True)
        (cert_dir / "ca.pem").write_text("fake")
        (cert_dir / "svc_fasttakapi.p12").write_bytes(b"fake")

        bootstrap.configure_tak_portal("test-token")

        settings = json.loads((tmp_path / "tak" / "portal" / "settings.json").read_text())
        prefixes = settings["USERS_HIDDEN_PREFIXES"]
        assert "svc_" in prefixes, "USERS_HIDDEN_PREFIXES should include svc_"
        assert "nodered-" not in prefixes, "Old nodered- prefix should be removed"
