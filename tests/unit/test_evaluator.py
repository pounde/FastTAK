"""Tests for app.evaluator — stateless threshold evaluation."""


class TestNumericEvaluation:
    def test_ok(self):
        from app.evaluator import evaluate

        result = evaluate(
            "database",
            {"size_bytes": 1000},
            {"thresholds": {"size_bytes": {"warning": 25000000000, "critical": 40000000000}}},
        )
        assert result["status"] == "ok"

    def test_warning(self):
        from app.evaluator import evaluate

        result = evaluate(
            "database",
            {"size_bytes": 30000000000},
            {"thresholds": {"size_bytes": {"warning": 25000000000, "critical": 40000000000}}},
        )
        assert result["status"] == "warning"
        assert "size_bytes" in result["message"]

    def test_critical(self):
        from app.evaluator import evaluate

        result = evaluate(
            "database",
            {"size_bytes": 50000000000},
            {"thresholds": {"size_bytes": {"warning": 25000000000, "critical": 40000000000}}},
        )
        assert result["status"] == "critical"

    def test_inverted_threshold_lower_is_worse(self):
        from app.evaluator import evaluate

        # warning=30 > critical=7 → lower is worse
        result = evaluate(
            "certs",
            {"items": [{"file": "ca.pem", "days_left": 5}]},
            {"thresholds": {"days_left": {"warning": 30, "critical": 7}}},
        )
        assert result["status"] == "critical"

    def test_inverted_threshold_warning(self):
        from app.evaluator import evaluate

        result = evaluate(
            "certs",
            {"items": [{"file": "ca.pem", "days_left": 20}]},
            {"thresholds": {"days_left": {"warning": 30, "critical": 7}}},
        )
        assert result["status"] == "warning"

    def test_inverted_threshold_ok(self):
        from app.evaluator import evaluate

        result = evaluate(
            "certs",
            {"items": [{"file": "ca.pem", "days_left": 60}]},
            {"thresholds": {"days_left": {"warning": 30, "critical": 7}}},
        )
        assert result["status"] == "ok"


class TestListEvaluation:
    def test_worst_item_wins(self):
        from app.evaluator import evaluate

        data = {
            "items": [
                {"mount": "root", "percent": 50},
                {"mount": "data", "percent": 92},
            ]
        }
        result = evaluate(
            "disk", data, {"thresholds": {"percent": {"warning": 80, "critical": 90}}}
        )
        assert result["status"] == "critical"
        assert "data" in result["message"]

    def test_all_ok(self):
        from app.evaluator import evaluate

        data = {"items": [{"mount": "root", "percent": 30}]}
        result = evaluate(
            "disk", data, {"thresholds": {"percent": {"warning": 80, "critical": 90}}}
        )
        assert result["status"] == "ok"

    def test_empty_items(self):
        from app.evaluator import evaluate

        result = evaluate(
            "disk", {"items": []}, {"thresholds": {"percent": {"warning": 80, "critical": 90}}}
        )
        assert result["status"] == "ok"


class TestBooleanEvaluation:
    def test_true_maps_to_note(self):
        from app.evaluator import evaluate

        result = evaluate(
            "config",
            {"changed": True},
            {"thresholds": {"changed": {"true": "note", "false": "ok"}}},
        )
        assert result["status"] == "note"

    def test_false_maps_to_ok(self):
        from app.evaluator import evaluate

        result = evaluate(
            "config",
            {"changed": False},
            {"thresholds": {"changed": {"true": "note", "false": "ok"}}},
        )
        assert result["status"] == "ok"

    def test_true_maps_to_warning(self):
        from app.evaluator import evaluate

        result = evaluate(
            "config",
            {"changed": True},
            {"thresholds": {"changed": {"true": "warning", "false": "ok"}}},
        )
        assert result["status"] == "warning"


class TestStateMappingEvaluation:
    """Container evaluation with two-map fallback: health → status."""

    _CONTAINER_CONFIG = {
        "thresholds": {
            "default_status": "warning",
            "health": {"healthy": "ok", "starting": "note", "unhealthy": "critical"},
            "status": {"running": "ok", "exited": "critical", "not_found": "critical"},
        }
    }

    def test_healthy_maps_to_ok(self):
        from app.evaluator import evaluate

        data = {"items": [{"name": "tak-server", "health": "healthy", "status": "running"}]}
        result = evaluate("containers", data, self._CONTAINER_CONFIG)
        assert result["status"] == "ok"

    def test_unhealthy_maps_to_critical(self):
        from app.evaluator import evaluate

        data = {"items": [{"name": "tak-server", "health": "unhealthy", "status": "running"}]}
        result = evaluate("containers", data, self._CONTAINER_CONFIG)
        assert result["status"] == "critical"
        assert "tak-server" in result["message"]

    def test_unknown_health_falls_back_to_status(self):
        from app.evaluator import evaluate

        data = {"items": [{"name": "mediamtx", "health": "unknown", "status": "running"}]}
        result = evaluate("containers", data, self._CONTAINER_CONFIG)
        assert result["status"] == "ok"  # falls back to status map: running → ok

    def test_exited_status_fallback(self):
        from app.evaluator import evaluate

        data = {"items": [{"name": "tak-server", "health": "unknown", "status": "exited"}]}
        result = evaluate("containers", data, self._CONTAINER_CONFIG)
        assert result["status"] == "critical"

    def test_unmapped_values_use_default_status(self):
        from app.evaluator import evaluate

        data = {"items": [{"name": "weird", "health": "unknown", "status": "paused"}]}
        result = evaluate("containers", data, self._CONTAINER_CONFIG)
        assert result["status"] == "warning"  # default_status


class TestAutovacuumFilter:
    def test_min_dead_tuples_filter(self):
        from app.evaluator import evaluate

        data = {
            "items": [
                {"table": "mission", "dead_tuples": 4, "dead_pct": 66.7},
                {"table": "cot_router", "dead_tuples": 50, "dead_pct": 0.1},
            ]
        }
        thresholds = {"dead_pct": {"warning": 5.0, "critical": 15.0}, "min_dead_tuples": 1000}
        result = evaluate("autovacuum", data, {"thresholds": thresholds})
        assert result["status"] == "ok"  # both below min_dead_tuples

    def test_above_min_dead_tuples_triggers(self):
        from app.evaluator import evaluate

        data = {
            "items": [
                {"table": "cot_router", "dead_tuples": 5000, "dead_pct": 8.0},
            ]
        }
        thresholds = {"dead_pct": {"warning": 5.0, "critical": 15.0}, "min_dead_tuples": 1000}
        result = evaluate("autovacuum", data, {"thresholds": thresholds})
        assert result["status"] == "warning"


class TestShouldAlert:
    def test_warning_alerts_by_default(self):
        from app.evaluator import evaluate

        result = evaluate(
            "database",
            {"size_bytes": 30000000000},
            {"thresholds": {"size_bytes": {"warning": 25000000000, "critical": 40000000000}}},
        )
        assert result["should_alert"] is True

    def test_ok_does_not_alert(self):
        from app.evaluator import evaluate

        result = evaluate(
            "database",
            {"size_bytes": 1000},
            {"thresholds": {"size_bytes": {"warning": 25000000000, "critical": 40000000000}}},
        )
        assert result["should_alert"] is False

    def test_note_alerts_when_min_level_is_note(self):
        from app.evaluator import evaluate

        result = evaluate(
            "config",
            {"changed": True},
            {
                "thresholds": {"changed": {"true": "note", "false": "ok"}},
                "alert_min_level": "note",
            },
        )
        assert result["should_alert"] is True

    def test_note_does_not_alert_when_min_level_is_warning(self):
        from app.evaluator import evaluate

        result = evaluate(
            "config",
            {"changed": True},
            {
                "thresholds": {"changed": {"true": "note", "false": "ok"}},
                "alert_min_level": "warning",
            },
        )
        assert result["should_alert"] is False
