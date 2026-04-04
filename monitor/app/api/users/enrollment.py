"""Enrollment URL construction for TAK device onboarding."""

from urllib.parse import quote, urlencode


def build_enrollment_url(token: str, server_address: str, username: str) -> str:
    """Build a tak:// enrollment URL from an app password token."""
    params = urlencode(
        {"host": server_address, "username": username, "token": token},
        quote_via=quote,
    )
    return f"tak://com.atakmap.app/enroll?{params}"
