"""Tests for LLDAP/Proxy identity client."""

import time
from unittest.mock import MagicMock, patch

import pytest
from app.api.users.identity import IdentityClient


@pytest.fixture
def client():
    return IdentityClient(
        lldap_url="http://lldap-test:17170",
        proxy_url="http://ldap-proxy-test:8081",
        admin_password="test-password",
        hidden_prefixes=["ak-", "adm_", "svc_", "ma-"],
    )


@pytest.fixture
def mock_http(client):
    """Patch the client's internal HTTP methods for clean testing."""
    with (
        patch.object(client, "_graphql") as mock_graphql,
        patch.object(client, "_proxy_request") as mock_proxy,
    ):
        yield {
            "graphql": mock_graphql,
            "proxy": mock_proxy,
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
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [{"id": 1, "displayName": "tak_ROLE_ADMIN"}],
                },
                {
                    "id": "svc_fasttakapi",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "Service",
                    "attributes": [],
                    "groups": [],
                },
                {
                    "id": "ak-admin",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "Admin",
                    "attributes": [],
                    "groups": [],
                },
            ],
        }
        users = client.list_users()
        assert len(users) == 1
        assert users[0]["username"] == "jsmith"

    def test_strips_tak_prefix_from_groups(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [
                        {"id": 1, "displayName": "tak_ROLE_ADMIN"},
                        {"id": 2, "displayName": "tak_Team Alpha"},
                        {"id": 3, "displayName": "lldap_admin"},  # non-tak, excluded
                    ],
                },
            ],
        }
        users = client.list_users()
        assert users[0]["groups"] == ["ROLE_ADMIN", "Team Alpha"]

    def test_search_filters_client_side(self, client, mock_http):
        # Search is done client-side (LLDAP only supports exact-match filters)
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "jsmith",
                    "displayName": "John Smith",
                    "creationDate": "2026-01-01",
                    "attributes": [],
                    "groups": [],
                },
                {
                    "id": "bjones",
                    "displayName": "Bob Jones",
                    "creationDate": "2026-01-01",
                    "attributes": [],
                    "groups": [],
                },
            ],
        }
        users = client.list_users(search="john")
        assert len(users) == 1
        assert users[0]["username"] == "jsmith"

    def test_returns_numeric_id(self, client, mock_http):
        """LLDAP users have both a string username (id) and a numeric ID.
        The _format_user must extract the numeric creationDate-based ID or
        use the LLDAP numeric UUID. For now we map via _user_id_map."""
        mock_http["graphql"].return_value = {
            "users": [
                {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [],
                    "uuid": "abc-123",
                },
            ],
        }
        users = client.list_users()
        # ID should be a stable integer
        assert isinstance(users[0]["id"], int)


class TestGetUser:
    def test_returns_user(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "user": {
                "id": "jsmith",
                "creationDate": "2024-01-01T00:00:00Z",
                "displayName": "John",
                "attributes": [
                    {"name": "fastak_expires", "value": ["9999999999"]},
                    {"name": "fastak_certs_revoked", "value": ["false"]},
                ],
                "groups": [{"id": 1, "displayName": "tak_ROLE_ADMIN"}],
            },
        }
        # We need the user_id_map to resolve numeric ID -> username
        client._user_id_map = {1: "jsmith"}
        user = client.get_user(1)
        assert user["username"] == "jsmith"
        assert user["fastak_expires"] == 9999999999
        assert user["groups"] == ["ROLE_ADMIN"]

    def test_returns_none_for_hidden_user(self, client, mock_http):
        mock_http["graphql"].return_value = {
            "user": {
                "id": "svc_fasttakapi",
                "creationDate": "2024-01-01T00:00:00Z",
                "displayName": "Service",
                "attributes": [],
                "groups": [],
            },
        }
        client._user_id_map = {2: "svc_fasttakapi"}
        assert client.get_user(2) is None

    def test_returns_none_for_missing_user(self, client, mock_http):
        mock_http["graphql"].return_value = {"user": None}
        client._user_id_map = {}
        assert client.get_user(999) is None


class TestCreateUser:
    def test_creates_passwordless_user(self, client, mock_http):
        mock_http["graphql"].side_effect = [
            # createUser mutation
            {"createUser": {"id": "newuser", "creationDate": "2024-01-01T00:00:00Z"}},
            # updateUser mutation (set attributes)
            {"updateUser": {"ok": True}},
            # list users to get numeric ID
            {
                "users": [
                    {
                        "id": "newuser",
                        "creationDate": "2024-01-01T00:00:00Z",
                        "displayName": "New User",
                        "attributes": [{"name": "fastak_certs_revoked", "value": ["false"]}],
                        "groups": [],
                    }
                ]
            },
        ]
        client.create_user("newuser", "New User")
        # First graphql call should be the createUser mutation
        create_call = mock_http["graphql"].call_args_list[0]
        query = create_call[0][0]
        assert "createUser" in query
        # No password in the create payload
        variables = create_call[0][1] if len(create_call[0]) > 1 else {}
        user_input = variables.get("user", {})
        assert "password" not in user_input

    def test_creates_user_with_ttl(self, client, mock_http):
        mock_http["graphql"].side_effect = [
            {"createUser": {"id": "temp", "creationDate": "2024-01-01T00:00:00Z"}},
            {"updateUser": {"ok": True}},
            {
                "users": [
                    {
                        "id": "temp",
                        "creationDate": "2024-01-01T00:00:00Z",
                        "displayName": "Temp",
                        "attributes": [
                            {
                                "name": "fastak_expires",
                                "value": [str(int(time.time() + 168 * 3600))],
                            },
                            {"name": "fastak_certs_revoked", "value": ["false"]},
                        ],
                        "groups": [],
                    }
                ]
            },
        ]
        client.create_user("temp", "Temp", ttl_hours=168)
        # Second call is updateUser with attributes
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        attr_names = [a["name"] for a in attrs]
        assert "fastak_expires" in attr_names
        assert "fastak_certs_revoked" in attr_names

    def test_creates_user_without_ttl_sets_revoked_false(self, client, mock_http):
        mock_http["graphql"].side_effect = [
            {"createUser": {"id": "perm", "creationDate": "2024-01-01T00:00:00Z"}},
            {"updateUser": {"ok": True}},
            {
                "users": [
                    {
                        "id": "perm",
                        "creationDate": "2024-01-01T00:00:00Z",
                        "displayName": "Perm",
                        "attributes": [{"name": "fastak_certs_revoked", "value": ["false"]}],
                        "groups": [],
                    }
                ]
            },
        ]
        client.create_user("perm", "Perm")
        # Second call sets attributes with fastak_certs_revoked = false
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        revoked_attr = next((a for a in attrs if a["name"] == "fastak_certs_revoked"), None)
        assert revoked_attr is not None
        assert revoked_attr["value"] == ["false"]


class TestDeactivateUser:
    def test_only_sets_inactive(self, client, mock_http):
        """deactivate_user should NOT set fastak_certs_revoked.
        The flag is managed by the caller after confirming cert revocation."""
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].return_value = {"updateUser": {"ok": True}}
        client.deactivate_user(1)
        call_args = mock_http["graphql"].call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else {}
        # Should NOT contain fastak_certs_revoked in the mutation
        assert "fastak_certs_revoked" not in str(variables)


class TestUpdateUser:
    def test_reactivation_resets_certs_revoked(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].side_effect = [
            # First: get user for existing attributes
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [
                        {"name": "fastak_certs_revoked", "value": ["true"]},
                    ],
                    "groups": [],
                },
            },
            # Second: updateUser mutation
            {"updateUser": {"ok": True}},
            # Third: get_user for return value (via _resolve_username -> get user)
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [
                        {"name": "fastak_certs_revoked", "value": ["false"]},
                    ],
                    "groups": [],
                },
            },
        ]
        client.update_user(1, is_active=True)
        # The update mutation should include fastak_certs_revoked = false
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        revoked_attr = next((a for a in attrs if a["name"] == "fastak_certs_revoked"), None)
        assert revoked_attr is not None
        assert revoked_attr["value"] == ["false"]

    def test_clear_ttl_clears_both_attrs(self, client, mock_http):
        """Sending ttl_hours=None should clear fastak_expires AND fastak_certs_revoked."""
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].side_effect = [
            # First: get user for existing attributes
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [
                        {"name": "fastak_expires", "value": ["999"]},
                        {"name": "fastak_certs_revoked", "value": ["false"]},
                    ],
                    "groups": [],
                },
            },
            # Second: updateUser mutation
            {"updateUser": {"ok": True}},
            # Third: get_user for return
            {
                "user": {
                    "id": "jsmith",
                    "creationDate": "2024-01-01T00:00:00Z",
                    "displayName": "John",
                    "attributes": [],
                    "groups": [],
                },
            },
        ]
        client.update_user(1, ttl_hours=None)
        # The update mutation should clear both attributes
        update_call = mock_http["graphql"].call_args_list[1]
        variables = update_call[0][1] if len(update_call[0]) > 1 else {}
        attrs = variables.get("input", {}).get("insertAttributes", [])
        # Both should be present but with empty values (LLDAP clears by setting to [])
        expires_attr = next((a for a in attrs if a["name"] == "fastak_expires"), None)
        revoked_attr = next((a for a in attrs if a["name"] == "fastak_certs_revoked"), None)
        assert expires_attr is not None
        assert expires_attr["value"] == []
        assert revoked_attr is not None
        assert revoked_attr["value"] == []


class TestEnrollmentTokens:
    def test_get_or_create_creates_when_none_exist(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        mock_http["proxy"].side_effect = [
            # GET tokens — none active
            MagicMock(status_code=200, json=lambda: {"tokens": []}),
            # POST create token
            MagicMock(
                status_code=201,
                json=lambda: {
                    "token": "secret-token-value",
                    "expires_at": 9999999999,
                    "one_time": True,
                    "username": "jsmith",
                },
            ),
        ]
        key, expires = client.get_or_create_enrollment_token(1, ttl_minutes=15)
        assert key == "secret-token-value"

    def test_get_or_create_returns_existing(self, client, mock_http):
        """If an active token exists, return it without creating a new one."""
        client._user_id_map = {1: "jsmith"}
        mock_http["proxy"].return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "tokens": [
                    {
                        "id": 1,
                        "token": "existing-token",
                        "expires_at": 9999999999,
                        "one_time": True,
                        "created_at": 1000,
                    }
                ]
            },
        )
        key, expires = client.get_or_create_enrollment_token(1, ttl_minutes=15)
        assert key == "existing-token"

    def test_delete_enrollment_tokens(self, client, mock_http):
        client._user_id_map = {1: "jsmith"}
        mock_http["graphql"].return_value = {
            "user": {
                "id": "jsmith",
                "creationDate": "2024-01-01T00:00:00Z",
                "displayName": "John",
                "attributes": [],
                "groups": [],
            },
        }
        mock_http["proxy"].return_value = MagicMock(status_code=200, json=lambda: {"deleted": 3})
        count = client.delete_enrollment_tokens(1)
        assert count == 3
