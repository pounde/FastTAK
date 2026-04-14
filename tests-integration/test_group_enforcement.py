"""Group assignment enforcement integration tests.

Verifies that the API enforces group rules based on fastak_user_type:
- Users and data service accounts require at least one group
- Admin service accounts cannot have groups
- Groups must exist before assignment
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def enforce_group_name(run_id):
    return f"ENFORCE_TST_{run_id}"


@pytest.fixture(scope="session")
def enforce_group(api, enforce_group_name, created_resources):
    """Create a group for enforcement tests."""
    status, data = api("POST", "/api/groups", {"name": enforce_group_name})
    assert status == 201, f"Failed to create enforcement test group: {data}"
    created_resources["enforce_group_id"] = data["id"]
    return enforce_group_name


@pytest.fixture(scope="session")
def enforce_user_name(run_id):
    return f"tste_{run_id}"


class TestBootstrapState:
    """Verify bootstrap created exactly the expected accounts and groups."""

    EXPECTED_SERVICE_ACCOUNTS = {"svc_fasttakapi"}
    EXPECTED_USERS = {"webadmin"}
    EXPECTED_GROUPS = {"ROLE_ADMIN"}

    def test_service_accounts(self, api):
        status, data = api("GET", "/api/service-accounts")
        assert status == 200
        usernames = {a["username"] for a in data.get("results", [])}
        assert usernames == self.EXPECTED_SERVICE_ACCOUNTS

    def test_users(self, api):
        status, data = api("GET", "/api/users")
        assert status == 200
        usernames = {u["username"] for u in data.get("results", [])}
        assert usernames == self.EXPECTED_USERS

    def test_groups(self, api):
        status, data = api("GET", "/api/groups")
        assert status == 200
        names = {g["name"] for g in data}
        assert names >= self.EXPECTED_GROUPS


class TestUserCreationGroupRequirement:
    """Users require at least one group at creation time."""

    def test_rejects_create_without_groups(self, api):
        status, data = api(
            "POST",
            "/api/users",
            {"username": "nogroups", "name": "No Groups"},
        )
        assert status == 422

    def test_rejects_create_with_empty_groups(self, api):
        status, data = api(
            "POST",
            "/api/users",
            {"username": "nogroups", "name": "No Groups", "groups": []},
        )
        assert status == 422

    def test_rejects_create_with_nonexistent_group(self, api):
        status, data = api(
            "POST",
            "/api/users",
            {"username": "nogroups", "name": "No Groups", "groups": ["DOES_NOT_EXIST"]},
        )
        assert status == 400
        assert "do not exist" in data["detail"].lower()

    @pytest.mark.dependency(name="enforce_user_create")
    def test_creates_user_with_valid_group(
        self,
        api,
        enforce_user_name,
        enforce_group,
        created_resources,
    ):
        status, data = api(
            "POST",
            "/api/users",
            {
                "username": enforce_user_name,
                "name": "Enforcement Test",
                "groups": [enforce_group],
            },
        )
        assert status == 201
        assert data["username"] == enforce_user_name
        created_resources["enforce_user_id"] = data["id"]


class TestGroupAssignmentEnforcement:
    """Group updates enforce type rules and existence checks."""

    @pytest.mark.dependency(depends=["enforce_user_create"])
    def test_rejects_empty_groups_for_user(self, api, created_resources):
        uid = created_resources["enforce_user_id"]
        status, data = api("PUT", f"/api/users/{uid}/groups", {"groups": []})
        assert status == 400
        assert "at least one group" in data["detail"].lower()

    @pytest.mark.dependency(depends=["enforce_user_create"])
    def test_rejects_nonexistent_groups_on_set(self, api, created_resources):
        uid = created_resources["enforce_user_id"]
        status, data = api(
            "PUT",
            f"/api/users/{uid}/groups",
            {"groups": ["DOES_NOT_EXIST"]},
        )
        assert status == 400
        assert "do not exist" in data["detail"].lower()


class TestAdminServiceAccountGroupEnforcement:
    """Admin service accounts cannot have groups assigned."""

    @pytest.mark.dependency(name="enforce_svc_admin_create")
    def test_create_admin_account(self, api, run_id, created_resources):
        status, data = api(
            "POST",
            "/api/service-accounts",
            {
                "name": f"tste_adm_{run_id}",
                "display_name": f"Enforce Admin {run_id}",
                "mode": "admin",
            },
        )
        assert status == 201
        created_resources["enforce_svc_admin_id"] = data["id"]

    @pytest.mark.dependency(depends=["enforce_svc_admin_create"])
    def test_rejects_groups_on_admin_patch(self, api, enforce_group, created_resources):
        sid = created_resources["enforce_svc_admin_id"]
        status, data = api(
            "PATCH",
            f"/api/service-accounts/{sid}",
            {"groups": [enforce_group]},
        )
        assert status == 400
        assert "admin" in data["detail"].lower()


class TestDataServiceAccountGroupEnforcement:
    """Data service accounts require at least one group."""

    @pytest.mark.dependency(name="enforce_svc_data_create")
    def test_create_data_account(self, api, run_id, enforce_group, created_resources):
        status, data = api(
            "POST",
            "/api/service-accounts",
            {
                "name": f"tste_dat_{run_id}",
                "display_name": f"Enforce Data {run_id}",
                "mode": "data",
                "groups": [enforce_group],
            },
        )
        assert status == 201
        created_resources["enforce_svc_data_id"] = data["id"]

    @pytest.mark.dependency(depends=["enforce_svc_data_create"])
    def test_rejects_empty_groups_on_data_patch(self, api, created_resources):
        sid = created_resources["enforce_svc_data_id"]
        status, data = api(
            "PATCH",
            f"/api/service-accounts/{sid}",
            {"groups": []},
        )
        assert status == 400
        assert "at least one group" in data["detail"].lower()
