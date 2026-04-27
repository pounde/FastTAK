"""Integration tests for /api/tak/* against a real Docker stack.

The test stack may or may not have connected clients / contacts; tests that
care about response structure assert shape but not content. Tests that care
about LKP enrichment short-circuit when the body is empty (no clients
connected during the test run).
"""

import pytest

pytestmark = pytest.mark.integration


class TestTakProxies:
    def test_groups_returns_200(self, api):
        status, data = api("GET", "/api/tak/groups")
        assert status == 200
        assert isinstance(data, list)

    def test_clients_returns_200(self, api):
        status, data = api("GET", "/api/tak/clients")
        assert status == 200
        assert isinstance(data, list)

    def test_contacts_returns_200(self, api):
        status, data = api("GET", "/api/tak/contacts")
        assert status == 200
        assert isinstance(data, list)

    def test_missions_returns_200(self, api):
        status, data = api("GET", "/api/tak/missions")
        assert status == 200
        assert isinstance(data, list)

    def test_clients_with_lkp_returns_lkp_field(self, api):
        status, data = api("GET", "/api/tak/clients?include=lkp")
        assert status == 200
        assert isinstance(data, list)
        # Conditional check: only assert lkp field if any clients are connected.
        if data:
            assert "lkp" in data[0]

    def test_contacts_recent_returns_200(self, api):
        status, data = api("GET", "/api/tak/contacts/recent?max_age=86400")
        assert status == 200
        assert isinstance(data, list)
        if data:
            assert "lkp" in data[0]  # may be None
