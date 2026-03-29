"""Tests for GET /api/health cached endpoint."""

from unittest.mock import patch


class TestHealthSummary:
    @patch("app.api.health.router.store")
    def test_full_response(self, mock_store, client):
        mock_store.fetch_all.return_value = {
            "database": {
                "status": "ok",
                "message": None,
                "data": {"size_bytes": 100},
                "thresholds": {},
                "updated_at": "2026-03-28T00:00:00Z",
            },
        }
        r = client.get("/api/health")
        data = r.json()
        assert r.status_code == 200
        assert "database" in data
        assert "overall" in data
        assert data["overall"] == "ok"

    @patch("app.api.health.router.store")
    def test_status_view(self, mock_store, client):
        mock_store.fetch_all.return_value = {
            "database": {
                "status": "warning",
                "message": "DB big",
                "data": {},
                "thresholds": {},
                "updated_at": "",
            },
            "disk": {
                "status": "ok",
                "message": None,
                "data": {},
                "thresholds": {},
                "updated_at": "",
            },
        }
        r = client.get("/api/health?view=status")
        data = r.json()
        assert data["overall"] == "warning"
        assert data["services"]["database"]["status"] == "warning"
        assert data["services"]["disk"]["status"] == "ok"

    @patch("app.api.health.router.store")
    def test_note_does_not_escalate_overall(self, mock_store, client):
        mock_store.fetch_all.return_value = {
            "updates": {
                "status": "note",
                "message": "Update available",
                "data": {},
                "thresholds": {},
                "updated_at": "",
            },
            "database": {
                "status": "ok",
                "message": None,
                "data": {},
                "thresholds": {},
                "updated_at": "",
            },
        }
        r = client.get("/api/health?view=status")
        assert r.json()["overall"] == "ok"
