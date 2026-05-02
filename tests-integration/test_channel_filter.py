"""Channel filter (group cache) tests.

TAK Server 5.6 only honors per-user channel toggles when the auth gate
in X509Authenticator passes. The gate requires either:

  - Cert EKU contains 1.2.840.113549.1.9.7 (added by /tls/signClient/v2
    when called with ?version=...), OR
  - x509useGroupCacheRequiresExtKeyUsage="false" in CoreConfig <auth>

FastTAK ships the latter (DD-042) as defense-in-depth against hard-cert
clients and clients that don't pass ?version=. These tests guard both
mechanisms — they catch regressions if upstream TAK Server changes the
gate semantics OR if init-config loses the attribute.
"""

from __future__ import annotations

import base64
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest
from cryptography import x509

CHANNELS_OID = "1.2.840.113549.1.9.7"
TAK_ENROLL_URL = "https://localhost:18446"

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_csr(tmp_path: Path, cn: str) -> Path:
    """Generate an RSA key + CSR with subject CN. Returns CSR path."""
    key_path = tmp_path / f"{cn}.key"
    csr_path = tmp_path / f"{cn}.csr"
    subprocess.run(
        ["openssl", "genrsa", "-out", str(key_path), "2048"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(key_path),
            "-out",
            str(csr_path),
            "-subj",
            f"/CN={cn}/O=FastTAK/OU=TAK",
        ],
        check=True,
        capture_output=True,
    )
    return csr_path


def _sign_client_cert(
    username: str,
    token: str,
    csr_path: Path,
    client_uid: str,
    *,
    with_version: bool,
) -> x509.Certificate:
    """Call /Marti/api/tls/signClient/v2 and return the signed X509 cert."""
    params = {"clientUid": client_uid}
    if with_version:
        params["version"] = "2"
    url = f"{TAK_ENROLL_URL}/Marti/api/tls/signClient/v2?{urllib.parse.urlencode(params)}"
    csr_bytes = csr_path.read_bytes()
    response = httpx.post(
        url,
        content=csr_bytes,
        headers={"Content-Type": "text/plain"},
        auth=(username, token),
        verify=False,
        timeout=30,
    )
    assert response.status_code == 200, (
        f"signClient/v2 failed for with_version={with_version}: "
        f"HTTP {response.status_code}, body={response.text[:300]}"
    )
    body = response.json()
    pem_b64 = body["signedCert"].replace("\n", "").replace(" ", "")
    der = base64.b64decode(pem_b64)
    return x509.load_der_x509_certificate(der)


def _eku_oids(cert: x509.Certificate) -> list[str]:
    """Return all EKU OID dotted-strings on the cert, or [] if no EKU ext."""
    try:
        ext = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return []
    return [oid.dotted_string for oid in ext.value]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def channel_test_group(api, run_id):
    """Group used by channel-filter test users. Self-cleans on teardown."""
    name = f"CHANTST_{run_id}"
    status, data = api("POST", "/api/groups", {"name": name})
    assert status == 201, f"Failed to create group: {data}"
    group_id = data["id"]
    yield name
    api("DELETE", f"/api/groups/{group_id}")


@pytest.fixture(scope="class")
def enrolled_user(api, run_id, channel_test_group):
    """Create a user, generate an enrollment token, yield (username, token).

    Self-cleans on teardown — uses yield + DELETE rather than registering with
    conftest.py's session-scoped cleanup_test_resources, which only knows about
    a fixed set of resource keys.
    """
    username = f"chantst_{run_id}"
    status, data = api(
        "POST",
        "/api/users",
        {"username": username, "name": "Channel Test", "groups": [channel_test_group]},
    )
    assert status == 201, f"Failed to create user: {data}"
    user_id = data["id"]

    status, data = api("POST", f"/api/users/{user_id}/enroll", None)
    assert status == 200, f"Failed to generate enrollment URL: {data}"
    token = urllib.parse.parse_qs(urllib.parse.urlparse(data["enrollment_url"]).query)["token"][0]

    yield username, token
    api("DELETE", f"/api/users/{user_id}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnrollmentEKU:
    """Validate upstream TAK Server's signClient/v2 EKU semantics.

    Guards against TAK Server changing how EKU injection is gated in a
    future release (which would break our assumption that ?version=...
    is the trigger).
    """

    def test_enrollment_without_version_lacks_oid(self, enrolled_user, tmp_path):
        username, token = enrolled_user
        csr = _generate_csr(tmp_path, username)
        cert = _sign_client_cert(username, token, csr, "test-uid-noversion", with_version=False)
        oids = _eku_oids(cert)
        assert oids, "Cert must have at least one EKU OID"
        assert CHANNELS_OID not in oids, (
            f"Cert enrolled without ?version= must NOT carry {CHANNELS_OID}; got EKU OIDs: {oids}"
        )

    def test_enrollment_with_version_carries_oid(self, enrolled_user, tmp_path):
        username, token = enrolled_user
        csr = _generate_csr(tmp_path, username)
        cert = _sign_client_cert(username, token, csr, "test-uid-withversion", with_version=True)
        oids = _eku_oids(cert)
        assert CHANNELS_OID in oids, (
            f"Cert enrolled with ?version=2 must carry {CHANNELS_OID}; got EKU OIDs: {oids}"
        )


class TestCoreConfigFlip:
    """Validate FastTAK's defense-in-depth.

    Guards against init-config losing the
    x509useGroupCacheRequiresExtKeyUsage="false" attribute (DD-042).
    """

    def test_auth_element_has_requires_eku_false(self, stack_info):
        config_path = Path(stack_info.tak_host_path) / "CoreConfig.xml"
        assert config_path.exists(), f"CoreConfig.xml not found at {config_path}"
        tree = ET.parse(config_path)
        root = tree.getroot()
        ns = {"m": "http://bbn.com/marti/xml/config"}
        auth = root.find("m:auth", ns)
        assert auth is not None, "No <auth> element found in CoreConfig.xml"
        attr_value = auth.attrib.get("x509useGroupCacheRequiresExtKeyUsage")
        assert attr_value == "false", (
            f'<auth> must have x509useGroupCacheRequiresExtKeyUsage="false" '
            f"per DD-042; got: {attr_value!r}"
        )
