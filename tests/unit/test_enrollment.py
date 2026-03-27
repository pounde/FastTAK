"""Tests for enrollment URL construction."""

from app.api.users.enrollment import build_enrollment_url


class TestBuildEnrollmentUrl:
    def test_basic_url(self):
        url = build_enrollment_url(token="abc123", fqdn="tak.example.com", port=8446)
        assert url.startswith("tak://")
        assert "tak.example.com" in url
        assert "8446" in url
        assert "abc123" in url

    def test_includes_token_param(self):
        url = build_enrollment_url(token="mytoken", fqdn="tak.example.com", port=8446)
        assert "mytoken" in url

    def test_different_port(self):
        url = build_enrollment_url(token="tok", fqdn="tak.example.com", port=9999)
        assert "9999" in url

    def test_special_characters_in_token(self):
        url = build_enrollment_url(token="tok+en/with=chars", fqdn="tak.example.com", port=8446)
        assert "tak://" in url
