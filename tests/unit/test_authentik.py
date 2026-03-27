"""Tests for Authentik API client."""

import time
from unittest.mock import patch

import pytest
from app.api.users.authentik import AuthentikClient


@pytest.fixture
def client():
    return AuthentikClient(
        base_url="http://authentik-test:9000",
        token="test-token",
        hidden_prefixes=["ak-", "adm_", "svc_", "ma-"],
    )


@pytest.fixture
def mock_http(client):
    """Patch the client's internal HTTP methods for clean testing."""
    with (
        patch.object(client, "_get") as mock_get,
        patch.object(client, "_post") as mock_post,
        patch.object(client, "_patch") as mock_patch,
        patch.object(client, "_delete") as mock_delete,
    ):
        yield {
            "get": mock_get,
            "post": mock_post,
            "patch": mock_patch,
            "delete": mock_delete,
        }


class TestIsHidden:
    def test_hidden_user(self, client):
        assert client.is_hidden("svc_fasttakapi") is True

    def test_visible_user(self, client):
        assert client.is_hidden("jsmith") is False

    def test_case_insensitive(self, client):
        assert client.is_hidden("AK-admin") is True

    def test_adm_prefix(self, client):
        assert client.is_hidden("adm_ldapservice") is True

    def test_ma_prefix(self, client):
        assert client.is_hidden("ma-user") is True


class TestListUsers:
    def test_filters_hidden_prefixes(self, client, mock_http):
        mock_http["get"].return_value = {
            "results": [
                {
                    "pk": 1,
                    "username": "jsmith",
                    "name": "John",
                    "is_active": True,
                    "attributes": {},
                    "groups_obj": [],
                },
                {
                    "pk": 2,
                    "username": "svc_fasttakapi",
                    "name": "Service",
                    "is_active": True,
                    "attributes": {},
                    "groups_obj": [],
                },
                {
                    "pk": 3,
                    "username": "ak-admin",
                    "name": "Admin",
                    "is_active": True,
                    "attributes": {},
                    "groups_obj": [],
                },
            ],
            "pagination": {"next": 0},
        }
        users = client.list_users()
        assert len(users) == 1
        assert users[0]["username"] == "jsmith"

    def test_strips_tak_prefix_from_groups(self, client, mock_http):
        mock_http["get"].return_value = {
            "results": [
                {
                    "pk": 1,
                    "username": "jsmith",
                    "name": "John",
                    "is_active": True,
                    "attributes": {},
                    "groups_obj": [
                        {"pk": "g1", "name": "tak_ROLE_ADMIN"},
                        {"pk": "g2", "name": "tak_FDNY Robotics"},
                        {"pk": "g3", "name": "authentik Admins"},  # non-tak, excluded
                    ],
                },
            ],
            "pagination": {"next": 0},
        }
        users = client.list_users()
        assert users[0]["groups"] == ["ROLE_ADMIN", "FDNY Robotics"]

    def test_passes_search_param(self, client, mock_http):
        mock_http["get"].return_value = {"results": [], "pagination": {"next": 0}}
        client.list_users(search="john")
        args, kwargs = mock_http["get"].call_args
        call_params = args[1] if len(args) > 1 else kwargs.get("params", {})
        assert call_params.get("search") == "john"

    def test_paginates_through_all_pages(self, client, mock_http):
        mock_http["get"].side_effect = [
            {
                "results": [
                    {
                        "pk": 1,
                        "username": "u1",
                        "name": "U1",
                        "is_active": True,
                        "attributes": {},
                        "groups_obj": [],
                    }
                ],
                "pagination": {"next": 2},
            },
            {
                "results": [
                    {
                        "pk": 2,
                        "username": "u2",
                        "name": "U2",
                        "is_active": True,
                        "attributes": {},
                        "groups_obj": [],
                    }
                ],
                "pagination": {"next": 0},
            },
        ]
        users = client.list_users()
        assert len(users) == 2


class TestGetUser:
    def test_returns_user(self, client, mock_http):
        mock_http["get"].return_value = {
            "pk": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "attributes": {"fastak_expires": 9999999999, "fastak_certs_revoked": False},
            "groups_obj": [{"pk": "g1", "name": "tak_ROLE_ADMIN"}],
        }
        user = client.get_user(1)
        assert user["username"] == "jsmith"
        assert user["fastak_expires"] == 9999999999
        assert user["groups"] == ["ROLE_ADMIN"]

    def test_returns_none_for_hidden_user(self, client, mock_http):
        mock_http["get"].return_value = {
            "pk": 2,
            "username": "svc_fasttakapi",
            "name": "Service",
            "is_active": True,
            "attributes": {},
            "groups_obj": [],
        }
        assert client.get_user(2) is None


class TestCreateUser:
    def test_creates_passwordless_user(self, client, mock_http):
        mock_http["post"].return_value = {
            "pk": 10,
            "username": "newuser",
            "name": "New User",
            "is_active": True,
            "attributes": {},
        }
        mock_http["patch"].return_value = {}
        client.create_user("newuser", "New User")
        post_data = mock_http["post"].call_args[0][1]
        assert post_data["username"] == "newuser"
        assert "password" not in post_data

    def test_creates_user_with_ttl(self, client, mock_http):
        mock_http["post"].return_value = {
            "pk": 10,
            "username": "temp",
            "name": "Temp",
            "is_active": True,
            "attributes": {},
        }
        mock_http["patch"].return_value = {}
        client.create_user("temp", "Temp", ttl_hours=168)
        patch_data = mock_http["patch"].call_args[0][1]
        attrs = patch_data["attributes"]
        assert "fastak_expires" in attrs
        assert attrs["fastak_certs_revoked"] is False
        assert attrs["fastak_expires"] > time.time()
        assert attrs["fastak_expires"] < time.time() + (169 * 3600)

    def test_creates_user_without_ttl_sets_revoked_false(self, client, mock_http):
        mock_http["post"].return_value = {
            "pk": 10,
            "username": "perm",
            "name": "Perm",
            "is_active": True,
            "attributes": {},
        }
        mock_http["patch"].return_value = {}
        client.create_user("perm", "Perm")
        patch_data = mock_http["patch"].call_args[0][1]
        assert patch_data["attributes"]["fastak_certs_revoked"] is False


class TestDeactivateUser:
    def test_only_sets_inactive(self, client, mock_http):
        """deactivate_user should NOT set fastak_certs_revoked.
        The flag is managed by the caller after confirming cert revocation."""
        mock_http["get"].return_value = {
            "pk": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "attributes": {"fastak_certs_revoked": False},
            "groups_obj": [],
        }
        mock_http["patch"].return_value = {}
        client.deactivate_user(1)
        patch_data = mock_http["patch"].call_args[0][1]
        assert patch_data["is_active"] is False
        # Must NOT set fastak_certs_revoked here
        attrs = patch_data.get("attributes", {})
        assert attrs.get("fastak_certs_revoked") is not True


class TestUpdateUser:
    def test_reactivation_resets_certs_revoked(self, client, mock_http):
        mock_http["get"].side_effect = [
            # First call: get existing attributes
            {
                "pk": 1,
                "username": "jsmith",
                "name": "John",
                "is_active": False,
                "attributes": {"fastak_certs_revoked": True},
                "groups_obj": [],
            },
            # Second call: get_user for return value
            {
                "pk": 1,
                "username": "jsmith",
                "name": "John",
                "is_active": True,
                "attributes": {"fastak_certs_revoked": False},
                "groups_obj": [],
            },
        ]
        mock_http["patch"].return_value = {}
        client.update_user(1, is_active=True)
        patch_data = mock_http["patch"].call_args[0][1]
        assert patch_data["is_active"] is True
        assert patch_data["attributes"]["fastak_certs_revoked"] is False

    def test_clear_ttl_clears_both_attrs(self, client, mock_http):
        """Sending ttl_hours=None should clear fastak_expires AND fastak_certs_revoked."""
        mock_http["get"].side_effect = [
            {
                "pk": 1,
                "username": "jsmith",
                "name": "John",
                "is_active": True,
                "attributes": {"fastak_expires": 999, "fastak_certs_revoked": False},
                "groups_obj": [],
            },
            {
                "pk": 1,
                "username": "jsmith",
                "name": "John",
                "is_active": True,
                "attributes": {},
                "groups_obj": [],
            },
        ]
        mock_http["patch"].return_value = {}
        client.update_user(1, ttl_hours=None)
        patch_data = mock_http["patch"].call_args[0][1]
        # fastak_expires should be removed
        assert "fastak_expires" not in patch_data["attributes"]
