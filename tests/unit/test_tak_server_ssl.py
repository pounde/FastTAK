"""TakServerClient pins the deployment CA instead of disabling verification.

The monitor connects to https://tak-server:8443 but the server cert is
issued for SERVER_ADDRESS, so hostname verification can never pass — that
is why the client went to CERT_NONE (#56). CERT_NONE also disables chain
verification, which lets anything on the Docker network impersonate TAK
Server to the monitor's admin credential. Chain verification against the
deployment's own ca.pem is the part that was never necessary to give up.
"""

import logging
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.api.users.tak_server import TakServerClient
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

PASSWORD = "atakatak"


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject: str, issuer_name: x509.Name, issuer_key, key, *, ca: bool):
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject))
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    return builder.sign(issuer_key, hashes.SHA256())


@pytest.fixture
def certs_dir(tmp_path: Path) -> Path:
    """A files/ directory like tak/certs/files: ca.pem plus a client p12."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca = _cert("FastTAK-Test-CA", _name("FastTAK-Test-CA"), ca_key, ca_key, ca=True)
    client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client = _cert("svc_fasttakapi", ca.subject, ca_key, client_key, ca=False)

    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "svc_fasttakapi.p12").write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"svc_fasttakapi",
            client_key,
            client,
            [ca],
            serialization.BestAvailableEncryption(PASSWORD.encode()),
        )
    )
    return tmp_path


def _client(certs_dir: Path) -> TakServerClient:
    return TakServerClient(
        base_url="https://tak-server:8443",
        cert_path=str(certs_dir / "svc_fasttakapi.p12"),
        cert_password=PASSWORD,
    )


def test_chain_verification_is_required_against_the_deployment_ca(certs_dir):
    c = _client(certs_dir)
    try:
        ctx = c._ssl_context
        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        loaded = [ca["subject"] for ca in ctx.get_ca_certs()]
        # Pinned, not augmented: the deployment CA is the only trust anchor.
        assert loaded == [((("commonName", "FastTAK-Test-CA"),),)]
        # ca.pem is TAK's intermediate; the chain only completes if an
        # intermediate may anchor it (default since Python 3.10).
        assert ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN
    finally:
        c.close()


def test_hostname_check_stays_off_because_the_cert_names_server_address(certs_dir):
    c = _client(certs_dir)
    try:
        assert c._ssl_context.check_hostname is False
    finally:
        c.close()


def test_missing_ca_fails_closed(certs_dir, caplog):
    """No CA means no way to verify — refuse to build a client rather than
    quietly falling back to CERT_NONE, which is the failure mode #57 names."""
    (certs_dir / "ca.pem").unlink()
    with caplog.at_level(logging.ERROR):
        c = _client(certs_dir)
    try:
        assert c._client is None
        assert c._ssl_context is None
        assert "ca.pem" in caplog.text
    finally:
        c.close()
