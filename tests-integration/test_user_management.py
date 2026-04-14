"""User management API integration tests.

Covers user CRUD, enrollment, deactivation, and reactivation.
Tests are ordered via pytest-dependency since they share a created user.
"""

import pytest

pytestmark = pytest.mark.integration


class TestUserManagement:
    @pytest.mark.dependency(name="user_list")
    def test_list_users(self, api):
        status, data = api("GET", "/api/users")
        assert status == 200
        assert data.get("count", -1) >= 0

    @pytest.mark.dependency(name="user_search")
    def test_search_users(self, api):
        status, data = api("GET", "/api/users?search=webadmin")
        assert status == 200
        assert data.get("count", 0) >= 1

    @pytest.mark.dependency(name="user_hidden")
    def test_hidden_prefix_excluded(self, api):
        status, data = api("GET", "/api/users?search=svc_")
        assert status == 200
        assert data.get("count", -1) == 0

    @pytest.mark.dependency(name="user_create")
    def test_create_user(self, api, test_user_name, user_group, created_resources):
        status, data = api(
            "POST",
            "/api/users",
            {"username": test_user_name, "name": "Test User", "groups": [user_group]},
        )
        assert status == 201
        assert data["username"] == test_user_name
        created_resources["test_user_id"] = data["id"]

    @pytest.mark.dependency(name="user_detail", depends=["user_create"])
    def test_get_user_detail(self, api, test_user_name, created_resources):
        uid = created_resources["test_user_id"]
        status, data = api("GET", f"/api/users/{uid}")
        assert status == 200
        assert data["username"] == test_user_name

    @pytest.mark.dependency(name="user_password", depends=["user_create"])
    def test_set_password(self, api, created_resources):
        uid = created_resources["test_user_id"]
        status, data = api(
            "POST",
            f"/api/users/{uid}/password",
            {"password": "TestPass123!"},
        )
        assert status == 200
        assert data.get("success") is True

    @pytest.mark.dependency(name="user_enroll", depends=["user_create"])
    def test_enroll_user(self, api, created_resources):
        uid = created_resources["test_user_id"]
        status, data = api("POST", f"/api/users/{uid}/enroll")
        assert status == 200
        assert "tak://" in data.get("enrollment_url", "")
        created_resources["enrollment_url"] = data["enrollment_url"]

    @pytest.mark.dependency(name="user_reenroll", depends=["user_enroll"])
    def test_reenroll_idempotent(self, api, created_resources):
        uid = created_resources["test_user_id"]
        status, data = api("POST", f"/api/users/{uid}/enroll")
        assert status == 200
        assert data["enrollment_url"] == created_resources["enrollment_url"]

    @pytest.mark.dependency(name="user_deactivate", depends=["user_reenroll"])
    def test_deactivate_user(self, api, created_resources):
        uid = created_resources["test_user_id"]
        status, data = api("DELETE", f"/api/users/{uid}")
        assert status == 200
        assert data.get("success") is True

    @pytest.mark.dependency(name="user_enroll_rejected", depends=["user_deactivate"])
    def test_enroll_rejected_when_deactivated(self, api, created_resources):
        uid = created_resources["test_user_id"]
        status, _data = api("POST", f"/api/users/{uid}/enroll")
        assert status == 400

    @pytest.mark.dependency(name="user_reactivate", depends=["user_enroll_rejected"])
    def test_reactivate_user(self, api, created_resources):
        uid = created_resources["test_user_id"]
        status, data = api("PATCH", f"/api/users/{uid}", {"is_active": True})
        assert status == 200
        assert data.get("is_active") is True

    @pytest.mark.dependency(depends=["user_reactivate"])
    def test_reenroll_after_reactivation(self, api, created_resources):
        uid = created_resources["test_user_id"]
        status, data = api("POST", f"/api/users/{uid}/enroll")
        assert status == 200
        assert "tak://" in data.get("enrollment_url", "")
