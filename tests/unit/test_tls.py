"""Tests for app.api.health.tls — TLS endpoint probing."""

import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _der_cert(days: int) -> bytes:
    """A self-signed cert expiring `days` from now (negative = already expired)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=abs(days) + 1))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.DER)


def _self_signed_pair(days: int) -> tuple[bytes, bytes, datetime]:
    """A private key + self-signed cert PEM pair expiring `days` from now
    (negative = already expired). Same builder as `_der_cert`, but keeps the
    key (needed to serve TLS) and returns PEM (needed for `load_cert_chain`)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    not_after = now + timedelta(days=days)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=abs(days) + 1))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem, not_after


def _probe_with(
    der: bytes | None, connect_error: Exception | None = None, deploy_mode="subdomain"
):
    """Drive _probe_tls_expiry with a fake socket whose peer cert is `der`."""
    ssock = MagicMock()
    ssock.getpeercert.return_value = der
    ctx = MagicMock()
    ctx.wrap_socket.return_value.__enter__ = MagicMock(return_value=ssock)
    ctx.wrap_socket.return_value.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.__enter__ = MagicMock()
    conn.__exit__ = MagicMock(return_value=False)
    with (
        patch("app.api.health.tls.ssl.create_default_context", return_value=ctx),
        patch(
            "app.api.health.tls.socket.create_connection",
            side_effect=connect_error,
            return_value=conn,
        ),
        patch("app.api.health.tls.settings") as settings,
    ):
        settings.deploy_mode = deploy_mode
        from app.api.health.tls import _probe_tls_expiry

        return _probe_tls_expiry("test.example.com"), ctx, ssock


class TestProbeTlsExpiry:
    def test_reads_expiry_from_the_der_cert_without_verifying(self):
        result, ctx, ssock = _probe_with(_der_cert(90))
        assert result["domain"] == "test.example.com"
        assert 88 <= result["days_left"] <= 90
        assert "error" not in result
        ssock.getpeercert.assert_called_once_with(binary_form=True)
        assert ctx.check_hostname is False
        import ssl

        assert ctx.verify_mode == ssl.CERT_NONE

    def test_subdomain_mode_also_skips_verification(self):
        """A verifying handshake raises on an expired cert — the one cert that
        matters most vanished from the list (#57). Expiry needs no trust."""
        _, ctx, _ = _probe_with(_der_cert(10), deploy_mode="subdomain")
        import ssl

        assert ctx.verify_mode == ssl.CERT_NONE

    def test_expired_cert_reports_negative_days(self):
        result, _, _ = _probe_with(_der_cert(-3))
        assert result["days_left"] < 0
        assert "error" not in result

    def test_connection_refused_is_an_error_item(self):
        result, _, _ = _probe_with(None, connect_error=ConnectionRefusedError())
        assert result == {"domain": "test.example.com", "error": "ConnectionRefusedError"}

    def test_empty_peer_cert_is_an_error_item(self):
        """getpeercert(binary_form=True) can be None/empty when the handshake
        gave no certificate; that is a reason, not a silent skip."""
        result, _, _ = _probe_with(b"")
        assert result == {"domain": "test.example.com", "error": "no certificate presented"}

    def test_long_transport_error_is_truncated_to_200_chars(self):
        long_message = "x" * 500
        result, _, _ = _probe_with(None, connect_error=OSError(long_message))
        assert len(result["error"]) == 200
        assert result["error"].startswith("OSError: " + "x" * 10)

    def test_port_is_part_of_the_domain_when_not_443(self):
        ssock = MagicMock()
        ssock.getpeercert.return_value = _der_cert(30)
        ctx = MagicMock()
        ctx.wrap_socket.return_value.__enter__ = MagicMock(return_value=ssock)
        ctx.wrap_socket.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch("app.api.health.tls.ssl.create_default_context", return_value=ctx),
            patch("app.api.health.tls.socket.create_connection"),
            patch("app.api.health.tls.settings") as settings,
        ):
            settings.deploy_mode = "direct"
            from app.api.health.tls import _probe_tls_expiry

            assert _probe_tls_expiry("tak.local", 8446)["domain"] == "tak.local:8446"


class TestGetTlsStatus:
    def test_returns_empty_for_localhost(self, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.tls.settings", mock_settings)
        mock_settings.server_address = "localhost"

        from app.api.health.tls import get_tls_status

        assert get_tls_status() == {"items": []}

    @patch("app.api.health.tls._probe_tls_expiry")
    def test_deduplicates_by_expiry(self, mock_probe, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.tls.settings", mock_settings)
        mock_settings.server_address = "example.com"

        # All subdomains return the same wildcard cert expiry
        mock_probe.return_value = {
            "domain": "takserver.example.com",
            "expires": "2027-03-23",
            "days_left": 365,
        }

        from app.api.health.tls import get_tls_status

        result = get_tls_status()
        # Should be deduplicated to 1 despite 5 endpoints probed
        assert len(result["items"]) == 1

    @patch("app.api.health.tls._probe_tls_expiry")
    def test_error_items_are_never_deduplicated_away(self, mock_probe, mock_settings, monkeypatch):
        monkeypatch.setattr("app.api.health.tls.settings", mock_settings)
        mock_settings.server_address = "example.com"
        mock_settings.deploy_mode = "subdomain"
        mock_probe.side_effect = [
            {"domain": "takserver.example.com", "expires": "2027-03-23", "days_left": 365},
            {"domain": "nodered.example.com", "error": "ConnectionRefusedError"},
            {"domain": "mediamtx.example.com", "expires": "2027-03-23", "days_left": 365},
        ]

        from app.api.health.tls import get_tls_status

        items = get_tls_status()["items"]
        assert len(items) == 2
        assert {"domain": "nodered.example.com", "error": "ConnectionRefusedError"} in items


class TestRealLoopbackHandshake:
    """No mocks: a real socket, a real TLS handshake, a real (self-signed,
    untrusted) cert served over 127.0.0.1. Proves the probe's CERT_NONE
    handshake actually works end to end, including against an already-expired
    cert — the case a verifying handshake cannot read (#57)."""

    def _probe_a_real_server(self, tmp_path, days: int) -> tuple[dict, datetime]:
        cert_pem, key_pem, not_after = _self_signed_pair(days)
        cert_path = tmp_path / "server.pem"
        key_path = tmp_path / "server.key"
        cert_path.write_bytes(cert_pem)
        key_path.write_bytes(key_pem)

        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.settimeout(5)  # bounded: a stuck accept() cannot hang the suite
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def _serve_one_connection():
            try:
                conn, _addr = listener.accept()
            except OSError:
                return
            try:
                with server_ctx.wrap_socket(conn, server_side=True) as tls_conn:
                    tls_conn.recv(1)
            except Exception:
                pass  # the client only wants the cert, not a full exchange
            finally:
                conn.close()

        server_thread = threading.Thread(target=_serve_one_connection, daemon=True)
        server_thread.start()
        try:
            from app.api.health.tls import _probe_tls_expiry

            result = _probe_tls_expiry("127.0.0.1", port)
        finally:
            listener.close()
            server_thread.join(timeout=5)  # bounded: cannot hang the suite
        return result, not_after

    def test_reads_expiry_from_a_real_tls_server_without_a_trust_store(self, tmp_path):
        result, not_after = self._probe_a_real_server(tmp_path, 90)
        assert "error" not in result
        expected_days = (not_after - datetime.now(UTC)).days
        assert abs(result["days_left"] - expected_days) <= 1

    def test_reads_expiry_from_an_already_expired_server_cert(self, tmp_path):
        """A verifying handshake would fail on an expired cert; the probe's
        CERT_NONE handshake completes and reports it as overdue instead."""
        result, _not_after = self._probe_a_real_server(tmp_path, -3)
        assert "error" not in result
        assert result["days_left"] < 0
