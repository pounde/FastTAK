"""Enrollment URL construction for TAK device onboarding."""

from urllib.parse import quote, urlencode


def build_enrollment_url(token: str, fqdn: str, port: int) -> str:
    """Build a tak:// enrollment URL from an app password token."""
    params = urlencode({"enrollment": token}, quote_via=quote)
    return f"tak://{fqdn}:{port}?{params}"
