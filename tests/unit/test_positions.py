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


def test_get_lkp_for_uids_decodes_bytes_columns():
    """SQL_ASCII columns come back as bytes from psycopg; result must be str."""
    from app.api.tak.positions import get_lkp_for_uids

    fake_rows = [
        (b"ANDROID-bytes", 38.8, -77.0, 100.0, "2026-04-27 12:00:00+00", b"a-f-G-U-C"),
    ]
    with patch("app.api.tak.positions.query", return_value=fake_rows):
        out = get_lkp_for_uids(["ANDROID-bytes"])

    assert "ANDROID-bytes" in out  # str key, not bytes
    assert out["ANDROID-bytes"]["uid"] == "ANDROID-bytes"
    assert out["ANDROID-bytes"]["cot_type"] == "a-f-G-U-C"


def test_get_recent_contacts_with_lkp_decodes_bytes_columns():
    from app.api.tak.positions import get_recent_contacts_with_lkp

    fake_rows = [
        (b"ANDROID-bytes", 38.8, -77.0, 100.0, "2026-04-27 12:00:00+00", b"a-f-G-U-C"),
    ]
    with patch("app.api.tak.positions.query", return_value=fake_rows):
        out = get_recent_contacts_with_lkp(["ANDROID-bytes"])

    assert out[0]["uid"] == "ANDROID-bytes"
    assert out[0]["cot_type"] == "a-f-G-U-C"


def test_parse_detail_returns_empty_for_none_or_empty():
    from app.api.tak.positions import _parse_detail

    assert _parse_detail(None) == {}
    assert _parse_detail("") == {}
    assert _parse_detail(b"") == {}


def test_parse_detail_extracts_callsign_team_role():
    from app.api.tak.positions import _parse_detail

    xml = (
        "<detail>"
        '<contact callsign="ALPHA-1" endpoint="*:-1:stcp"/>'
        '<__group name="Cyan" role="Team Lead"/>'
        '<takv platform="ATAK-CIV" version="5.1.0"/>'
        "</detail>"
    )
    out = _parse_detail(xml)
    assert out == {"callsign": "ALPHA-1", "team": "Cyan", "role": "Team Lead"}


def test_parse_detail_handles_bytes_input():
    """SQL_ASCII columns can come back as bytes from psycopg."""
    from app.api.tak.positions import _parse_detail

    xml = b'<detail><contact callsign="BRAVO-2"/></detail>'
    assert _parse_detail(xml) == {"callsign": "BRAVO-2"}


def test_parse_detail_omits_missing_fields():
    from app.api.tak.positions import _parse_detail

    # No __group element
    out = _parse_detail('<detail><contact callsign="ALPHA-1"/></detail>')
    assert out == {"callsign": "ALPHA-1"}
    # __group present but missing one attr
    out = _parse_detail('<detail><__group name="Cyan"/></detail>')
    assert out == {"team": "Cyan"}


def test_parse_detail_swallows_malformed_xml():
    from app.api.tak.positions import _parse_detail

    # Unclosed tag — would raise ET.ParseError
    assert _parse_detail("<detail><contact callsign='oops'") == {}
    # Total garbage
    assert _parse_detail("not xml at all") == {}


def test_parse_detail_handles_no_root_element():
    """Some TAK clients emit detail fragments without a wrapper."""
    from app.api.tak.positions import _parse_detail

    xml = '<contact callsign="SOLO"/>'
    out = _parse_detail(xml)
    # Either {} or {"callsign": "SOLO"} is acceptable; we accept both,
    # but the parser should NOT raise. Pin the actual behavior here.
    assert out in ({}, {"callsign": "SOLO"})
