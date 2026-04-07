"""Full user lifecycle integration tests.

Create -> search -> update -> password -> enroll -> deactivate -> reactivate.
"""

import pytest

pytestmark = pytest.mark.integration


class TestUserLifecycle:
    @pytest.mark.dependency(name="lifecycle_create")
    def test_create_user(self, api, test_lifecycle_user_name, run_id, created_resources):
        status, data = api(
            "POST",
            "/api/users",
            {
                "username": test_lifecycle_user_name,
                "name": f"Lifecycle {run_id}",
            },
        )
        assert status == 201
        assert data["username"] == test_lifecycle_user_name
        assert data["name"] == f"Lifecycle {run_id}"
        created_resources["lifecycle_user_id"] = data["id"]

    @pytest.mark.dependency(name="lifecycle_search", depends=["lifecycle_create"])
    def test_search_finds_user(self, api, test_lifecycle_user_name):
        status, data = api("GET", f"/api/users?search={test_lifecycle_user_name}")
        assert status == 200
        assert data.get("count", 0) >= 1
        usernames = [u["username"] for u in data["results"]]
        assert test_lifecycle_user_name in usernames

    @pytest.mark.dependency(name="lifecycle_update", depends=["lifecycle_create"])
    def test_update_name(self, api, run_id, created_resources):
        uid = created_resources["lifecycle_user_id"]
        status, data = api(
            "PATCH",
            f"/api/users/{uid}",
            {"name": f"Updated {run_id}"},
        )
        assert status == 200
        assert data["name"] == f"Updated {run_id}"

    @pytest.mark.dependency(name="lifecycle_verify_update", depends=["lifecycle_update"])
    def test_verify_name_update(self, api, run_id, created_resources):
        uid = created_resources["lifecycle_user_id"]
        status, data = api("GET", f"/api/users/{uid}")
        assert status == 200
        assert data["name"] == f"Updated {run_id}"

    @pytest.mark.dependency(name="lifecycle_password", depends=["lifecycle_create"])
    def test_set_password(self, api, created_resources):
        uid = created_resources["lifecycle_user_id"]
        status, data = api(
            "POST",
            f"/api/users/{uid}/password",
            {"password": "TestPass123!"},
        )
        assert status == 200
        assert data.get("success") is True

    @pytest.mark.dependency(name="lifecycle_enroll", depends=["lifecycle_create"])
    def test_enroll(self, api, created_resources):
        uid = created_resources["lifecycle_user_id"]
        status, data = api("POST", f"/api/users/{uid}/enroll")
        assert status == 200
        assert "tak://" in data.get("enrollment_url", "")

    @pytest.mark.dependency(name="lifecycle_deactivate", depends=["lifecycle_enroll"])
    def test_deactivate(self, api, created_resources):
        uid = created_resources["lifecycle_user_id"]
        status, data = api("DELETE", f"/api/users/{uid}")
        assert status == 200
        assert data.get("success") is True

    @pytest.mark.dependency(
        name="lifecycle_verify_deactivated",
        depends=["lifecycle_deactivate"],
    )
    def test_verify_deactivated(self, api, created_resources):
        uid = created_resources["lifecycle_user_id"]
        status, data = api("GET", f"/api/users/{uid}")
        assert status == 200
        assert data["is_active"] is False

    @pytest.mark.dependency(
        name="lifecycle_reactivate",
        depends=["lifecycle_verify_deactivated"],
    )
    def test_reactivate(self, api, created_resources):
        uid = created_resources["lifecycle_user_id"]
        status, data = api("PATCH", f"/api/users/{uid}", {"is_active": True})
        assert status == 200
        assert data["is_active"] is True

    @pytest.mark.dependency(depends=["lifecycle_reactivate"])
    def test_verify_reactivated(self, api, created_resources):
        uid = created_resources["lifecycle_user_id"]
        status, data = api("GET", f"/api/users/{uid}")
        assert status == 200
        assert data["is_active"] is True
