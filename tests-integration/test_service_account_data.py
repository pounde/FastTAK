"""Service account lifecycle (data mode) integration tests."""

import pytest

pytestmark = pytest.mark.integration


class TestServiceAccountData:
    @pytest.mark.dependency(name="svc_data_group")
    def test_create_group(self, api, svc_test_group_name, created_resources):
        """Create a group needed for data-mode service accounts."""
        status, data = api("POST", "/api/groups", {"name": svc_test_group_name})
        assert status == 201
        created_resources["svc_group_id"] = data["id"]

    @pytest.mark.dependency(name="svc_data_create", depends=["svc_data_group"])
    def test_create_data_account(
        self,
        api,
        svc_data_name,
        svc_test_group_name,
        run_id,
        created_resources,
    ):
        status, data = api(
            "POST",
            "/api/service-accounts",
            {
                "name": svc_data_name,
                "display_name": f"Data {run_id}",
                "mode": "data",
                "groups": [svc_test_group_name],
            },
        )
        assert status == 201
        assert data["username"] == f"svc_{svc_data_name}"
        assert data["mode"] == "data"
        assert "/api/service-accounts/" in data.get("cert_download_url", "")
        created_resources["svc_data_id"] = data["id"]

    @pytest.mark.dependency(depends=["svc_data_create"])
    def test_appears_in_list(self, api, svc_data_name):
        status, data = api("GET", "/api/service-accounts")
        assert status == 200
        usernames = [a["username"] for a in data.get("results", [])]
        assert f"svc_{svc_data_name}" in usernames

    @pytest.mark.dependency(depends=["svc_data_create"])
    def test_download_cert(self, api, created_resources):
        sid = created_resources["svc_data_id"]
        status, _data = api("GET", f"/api/service-accounts/{sid}/certs/download")
        assert status == 200

    @pytest.mark.dependency(depends=["svc_data_create"])
    def test_detail_includes_certs(self, api, svc_data_name, created_resources):
        sid = created_resources["svc_data_id"]
        status, data = api("GET", f"/api/service-accounts/{sid}")
        assert status == 200
        assert data["username"] == f"svc_{svc_data_name}"
        assert isinstance(data.get("certs"), list)

    @pytest.mark.dependency(name="svc_data_update", depends=["svc_data_create"])
    def test_update_display_name(self, api, created_resources):
        sid = created_resources["svc_data_id"]
        status, data = api(
            "PATCH",
            f"/api/service-accounts/{sid}",
            {"display_name": "Updated Data Name"},
        )
        assert status == 200
        assert data.get("success") is True

    @pytest.mark.dependency(name="svc_data_deactivate", depends=["svc_data_update"])
    def test_deactivate(self, api, svc_data_name, created_resources):
        sid = created_resources["svc_data_id"]
        status, data = api("DELETE", f"/api/service-accounts/{sid}")
        assert status == 200
        assert data.get("success") is True
        assert data["username"] == f"svc_{svc_data_name}"

    @pytest.mark.dependency(depends=["svc_data_deactivate"])
    def test_verify_deactivated(self, api, created_resources):
        sid = created_resources["svc_data_id"]
        status, data = api("GET", f"/api/service-accounts/{sid}")
        assert status == 200
        assert data["is_active"] is False
