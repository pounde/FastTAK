"""Tests for user management API routes."""

from unittest.mock import MagicMock

import pytest
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_clients(monkeypatch):
    """Mock the Authentik and TAK Server clients used by the router."""
    mock_ak = MagicMock()
    mock_tak = MagicMock()
    monkeypatch.setattr("app.api.users.router._authentik", mock_ak)
    monkeypatch.setattr("app.api.users.router._tak_server", mock_tak)
    return mock_ak, mock_tak


# ── Users ─────────────────────────────────────────────────────────


class TestListUsers:
    def test_returns_paginated_users(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_users.return_value = [
            {"id": i, "username": f"user{i}", "name": f"U{i}", "is_active": True, "groups": []}
            for i in range(60)
        ]
        resp = client.get("/api/users?page=2&page_size=50")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["count"] == 60
        assert len(data["results"]) == 10

    def test_search_param(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_users.return_value = []
        client.get("/api/users?search=john")
        mock_ak.list_users.assert_called_once_with(search="john")

    def test_include_certs_enriches_response(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.list_users.return_value = [
            {"id": 1, "username": "jsmith", "name": "John", "is_active": True, "groups": []},
        ]
        mock_tak.list_user_certs.return_value = [
            {
                "id": 42,
                "hash": "abc",
                "issuance_date": "2026-03-27",
                "expiration_date": "2027-03-27",
                "revocation_date": None,
            },
        ]
        resp = client.get("/api/users?include=certs")
        assert resp.status_code == 200
        assert "certs" in resp.json()["results"][0]
        mock_tak.list_user_certs.assert_called_once_with("jsmith")

    def test_no_certs_key_without_include(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.list_users.return_value = [
            {"id": 1, "username": "jsmith", "name": "John", "is_active": True, "groups": []},
        ]
        resp = client.get("/api/users")
        assert "certs" not in resp.json()["results"][0]
        mock_tak.list_user_certs.assert_not_called()

    def test_503_when_no_token(self, monkeypatch):
        monkeypatch.setattr("app.api.users.router._authentik", None)
        from app.config import Settings

        monkeypatch.setattr("app.config.settings", Settings(authentik_api_token=""))
        resp = client.get("/api/users")
        assert resp.status_code == 503


class TestGetUser:
    def test_returns_user_with_certs(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.list_user_certs.return_value = [{"id": 42}]
        resp = client.get("/api/users/1")
        assert resp.status_code == 200
        assert "certs" in resp.json()

    def test_404_for_missing_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.get("/api/users/999")
        assert resp.status_code == 404


class TestCreateUser:
    def test_creates_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 10,
            "username": "newuser",
            "name": "New",
            "is_active": True,
            "groups": [],
        }
        resp = client.post("/api/users", json={"username": "newuser", "name": "New"})
        assert resp.status_code == 201

    def test_creates_user_with_ttl(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_user.return_value = {
            "id": 10,
            "username": "temp",
            "name": "Temp",
            "is_active": True,
            "groups": [],
            "fastak_expires": 9999999999,
        }
        resp = client.post(
            "/api/users", json={"username": "temp", "name": "Temp", "ttl_hours": 168}
        )
        assert resp.status_code == 201

    def test_rejects_invalid_username(self, mock_clients):
        resp = client.post("/api/users", json={"username": "bad user!", "name": "Bad"})
        assert resp.status_code == 422


class TestDeleteUser:
    def test_deactivates_and_revokes(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.revoke_all_user_certs.return_value = True
        resp = client.delete("/api/users/1")
        assert resp.status_code == 200
        mock_ak.deactivate_user.assert_called_once_with(1)
        mock_tak.revoke_all_user_certs.assert_called_once_with("jsmith")
        mock_ak.mark_certs_revoked.assert_called_once_with(1)

    def test_does_not_mark_revoked_on_failure(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.revoke_all_user_certs.return_value = False
        resp = client.delete("/api/users/1")
        assert resp.status_code == 200
        mock_ak.deactivate_user.assert_called_once()
        mock_ak.mark_certs_revoked.assert_not_called()

    def test_404_for_hidden_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.delete("/api/users/1")
        assert resp.status_code == 404


class TestUpdateUser:
    def test_patch_ttl_null_clears_ttl(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_ak.update_user.return_value = {"id": 1, "username": "jsmith", "is_active": True}
        resp = client.patch("/api/users/1", json={"ttl_hours": None})
        assert resp.status_code == 200
        mock_ak.update_user.assert_called_once()
        kwargs = mock_ak.update_user.call_args[1]
        assert kwargs["ttl_hours"] is None

    def test_patch_name_only_does_not_touch_ttl(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_ak.update_user.return_value = {"id": 1, "username": "jsmith"}
        resp = client.patch("/api/users/1", json={"name": "New Name"})
        assert resp.status_code == 200
        kwargs = mock_ak.update_user.call_args[1]
        assert "ttl_hours" not in kwargs


# ── Password ──────────────────────────────────────────────────────


class TestSetPassword:
    def test_sets_password(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        resp = client.post("/api/users/1/password", json={"password": "secret123"})
        assert resp.status_code == 200
        mock_ak.set_password.assert_called_once_with(1, "secret123")

    def test_400_for_deactivated_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": False,
            "groups": [],
        }
        resp = client.post("/api/users/1/password", json={"password": "secret"})
        assert resp.status_code == 400

    def test_404_for_hidden_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = None
        resp = client.post("/api/users/1/password", json={"password": "secret"})
        assert resp.status_code == 404


# ── Enrollment ────────────────────────────────────────────────────


class TestEnrollUser:
    def test_generates_enrollment_url(self, mock_clients, mock_settings):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_ak.get_or_create_enrollment_token.return_value = ("abc123", "2026-03-27T12:15:00Z")
        resp = client.post("/api/users/1/enroll")
        assert resp.status_code == 200
        data = resp.json()
        assert "tak://" in data["enrollment_url"]
        assert data["expires_at"] == "2026-03-27T12:15:00Z"

    def test_400_for_deactivated_user(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": False,
            "groups": [],
        }
        resp = client.post("/api/users/1/enroll")
        assert resp.status_code == 400


# ── Certs ─────────────────────────────────────────────────────────


class TestUserCerts:
    def test_list_certs(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.list_user_certs.return_value = [{"id": 42}]
        resp = client.get("/api/users/1/certs")
        assert resp.status_code == 200
        assert resp.json() == [{"id": 42}]

    def test_revoke_cert(self, mock_clients):
        mock_ak, mock_tak = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        mock_tak.revoke_cert.return_value = True
        resp = client.post("/api/users/1/certs/revoke", json={"cert_id": 42})
        assert resp.status_code == 200
        mock_tak.revoke_cert.assert_called_once_with(42)


# ── Groups ────────────────────────────────────────────────────────


class TestGroups:
    def test_list_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.list_groups.return_value = [{"id": "g1", "name": "ROLE_ADMIN"}]
        resp = client.get("/api/groups")
        assert resp.status_code == 200
        assert resp.json() == [{"id": "g1", "name": "ROLE_ADMIN"}]

    def test_create_group(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.create_group.return_value = {"id": "g2", "name": "FDNY Robotics"}
        resp = client.post("/api/groups", json={"name": "FDNY Robotics"})
        assert resp.status_code == 201

    def test_get_group_detail(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_group.return_value = {
            "id": "g1",
            "name": "ROLE_ADMIN",
            "members": [{"id": 1, "username": "jsmith"}],
        }
        resp = client.get("/api/groups/g1")
        assert resp.status_code == 200
        assert "members" in resp.json()

    def test_delete_group(self, mock_clients):
        mock_ak, _ = mock_clients
        resp = client.delete("/api/groups/g1")
        assert resp.status_code == 200
        mock_ak.delete_group.assert_called_once_with("g1")

    def test_set_user_groups(self, mock_clients):
        mock_ak, _ = mock_clients
        mock_ak.get_user.return_value = {
            "id": 1,
            "username": "jsmith",
            "name": "John",
            "is_active": True,
            "groups": [],
        }
        resp = client.put("/api/users/1/groups", json={"groups": ["ROLE_ADMIN", "FDNY Robotics"]})
        assert resp.status_code == 200
        mock_ak.set_user_groups.assert_called_once_with(1, ["ROLE_ADMIN", "FDNY Robotics"])
