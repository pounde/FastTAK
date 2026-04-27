"""Tests for cot_router-backed LKP queries."""

from unittest.mock import patch


def test_get_lkp_for_uids_maps_rows_to_positions():
    from app.api.tak.positions import get_lkp_for_uids

    fake_rows = [
        ("ANDROID-abc", 38.8, -77.0, 100.0, "2026-04-27 12:00:00+00", "a-f-G-U-C"),
        ("ANDROID-def", 38.9, -77.1, 50.0, "2026-04-27 11:55:00+00", "a-f-G-U-C"),
    ]
    with patch("app.api.tak.positions.query", return_value=fake_rows) as mock_query:
        out = get_lkp_for_uids(["ANDROID-abc", "ANDROID-def"])

    sql, params = mock_query.call_args.args
    assert "DISTINCT ON (uid)" in sql
    assert params == ("ANDROID-abc", "ANDROID-def")
    assert set(out.keys()) == {"ANDROID-abc", "ANDROID-def"}
    assert out["ANDROID-abc"]["lat"] == 38.8
    assert out["ANDROID-abc"]["cot_type"] == "a-f-G-U-C"


def test_get_lkp_for_uids_empty_input_skips_query():
    from app.api.tak.positions import get_lkp_for_uids

    with patch("app.api.tak.positions.query") as mock_query:
        out = get_lkp_for_uids([])
    assert out == {}
    mock_query.assert_not_called()


def test_get_recent_contacts_with_lkp_filters_by_uids_only_when_no_max_age():
    from app.api.tak.positions import get_recent_contacts_with_lkp

    fake_rows = [
        ("ANDROID-abc", 38.8, -77.0, 100.0, "2026-04-27 12:00:00+00", "a-f-G-U-C"),
    ]
    with patch("app.api.tak.positions.query", return_value=fake_rows) as mock_query:
        out = get_recent_contacts_with_lkp(["ANDROID-abc", "ANDROID-def"])

    sql, params = mock_query.call_args.args
    assert params == ("ANDROID-abc", "ANDROID-def")
    assert "make_interval" not in sql
    assert len(out) == 1
    assert out[0]["uid"] == "ANDROID-abc"


def test_get_recent_contacts_with_lkp_applies_max_age_when_set():
    from app.api.tak.positions import get_recent_contacts_with_lkp

    fake_rows = []
    with patch("app.api.tak.positions.query", return_value=fake_rows) as mock_query:
        get_recent_contacts_with_lkp(["ANDROID-abc"], max_age_seconds=3600)

    sql, params = mock_query.call_args.args
    assert params == ("ANDROID-abc", 3600)
    assert "make_interval" in sql


def test_get_recent_contacts_with_lkp_empty_input_skips_query():
    from app.api.tak.positions import get_recent_contacts_with_lkp

    with patch("app.api.tak.positions.query") as mock_query:
        out = get_recent_contacts_with_lkp([])
    assert out == []
    mock_query.assert_not_called()
