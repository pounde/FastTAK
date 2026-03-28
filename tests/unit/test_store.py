"""Tests for app.store — in-memory health cache."""


class TestStore:
    def setup_method(self):
        import app.store as store_mod

        store_mod._cache = {}

    def test_update_and_fetch(self):
        from app.store import fetch, update

        update(
            "database", {"size_bytes": 100}, {"status": "ok"}, {"size_bytes": {"warning": 1000}}
        )
        entry = fetch("database")
        assert entry is not None
        assert entry["status"] == "ok"
        assert entry["data"]["size_bytes"] == 100
        assert entry["thresholds"]["size_bytes"]["warning"] == 1000
        assert "updated_at" in entry

    def test_fetch_missing(self):
        from app.store import fetch

        assert fetch("nonexistent") is None

    def test_fetch_all(self):
        from app.store import fetch_all, update

        update("a", {"val": 1}, {"status": "ok"}, None)
        update("b", {"val": 2}, {"status": "warning", "message": "high"}, {"x": {"warning": 1}})
        result = fetch_all()
        assert "a" in result
        assert "b" in result
        assert result["b"]["message"] == "high"

    def test_field_ordering(self):
        from app.store import fetch, update

        update("test", {"x": 1}, {"status": "warning", "message": "msg"}, {"x": {"warning": 1}})
        entry = fetch("test")
        keys = list(entry.keys())
        assert keys == ["status", "message", "data", "thresholds", "updated_at"]

    def test_none_thresholds_on_error(self):
        from app.store import fetch, update

        update("test", {"error": "failed"}, {"status": "critical", "message": "failed"}, None)
        entry = fetch("test")
        assert entry["thresholds"] is None
