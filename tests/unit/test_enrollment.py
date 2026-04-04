"""Tests for enrollment URL construction."""

from app.api.users.enrollment import build_enrollment_url


class TestBuildEnrollmentUrl:
    def test_basic_url(self):
        url = build_enrollment_url(
            token="abc123", server_address="tak.example.com", username="jdoe"
        )
        assert (
            url == "tak://com.atakmap.app/enroll?host=tak.example.com&username=jdoe&token=abc123"
        )

    def test_includes_all_params(self):
        url = build_enrollment_url(
            token="mytoken", server_address="tak.example.com", username="epound"
        )
        assert "host=tak.example.com" in url
        assert "username=epound" in url
        assert "token=mytoken" in url

    def test_special_characters_in_token(self):
        url = build_enrollment_url(
            token="tok+en/with=chars", server_address="tak.example.com", username="user1"
        )
        assert "tak://com.atakmap.app/enroll?" in url
        assert "username=user1" in url
