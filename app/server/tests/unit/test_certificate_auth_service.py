# REQ: SWR-001, SWR-034; RISK: RISK-002, RISK-019; SEC: SC-001, SC-017; TEST: TC-004, TC-032
"""Unit tests for local-CA client certificate authentication."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from monitor.services.certificate_auth_service import (
    CERT_HEADER,
    SERIAL_HEADER,
    VERIFY_HEADER,
    CertificateAuthService,
)


def _client_cert(
    *,
    common_name="service-laptop-01",
    profile="owner-admin",
    client_auth=True,
    serial=1001,
    not_before=None,
    not_after=None,
):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    not_before = not_before or (now - timedelta(minutes=5))
    not_after = not_after or (now + timedelta(days=1))
    eku = (
        ExtendedKeyUsageOID.CLIENT_AUTH
        if client_auth
        else ExtendedKeyUsageOID.SERVER_AUTH
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Home Monitor Test"),
                ]
            )
        )
        .issuer_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, "Home Monitor Test CA"),
                ]
            )
        )
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"urn:home-monitor:profile:{profile}")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return cert, pem


def _ca_cert(*, common_name="Home Monitor Test CA"):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return key, cert, pem


def _client_cert_signed_by_ca(ca_key, ca_cert, *, common_name="service-laptop-01"):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Home Monitor Test"),
                ]
            )
        )
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(1001)
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier("urn:home-monitor:profile:owner-admin")]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    return cert, pem


def _headers(pem, *, serial="3E9", verify="SUCCESS"):
    return {
        VERIFY_HEADER: verify,
        CERT_HEADER: quote(pem),
        SERIAL_HEADER: serial,
    }


def test_rejects_unverified_certificate(tmp_path):
    _cert, pem = _client_cert()
    svc = CertificateAuthService(config_dir=str(tmp_path), allow_profile_login=True)

    principal, error = svc.authenticate_headers(_headers(pem, verify="FAILED"))

    assert principal is None
    assert error == "client certificate was not verified"


def test_rejects_missing_client_auth_eku(tmp_path):
    _cert, pem = _client_cert(client_auth=False)
    svc = CertificateAuthService(config_dir=str(tmp_path), allow_profile_login=True)

    principal, error = svc.authenticate_headers(_headers(pem))

    assert principal is None
    assert "client authentication" in error


def test_rejects_unsupported_profile(tmp_path):
    _cert, pem = _client_cert(profile="not-home-monitor")
    svc = CertificateAuthService(config_dir=str(tmp_path), allow_profile_login=True)

    principal, error = svc.authenticate_headers(_headers(pem))

    assert principal is None
    assert "profile" in error


def test_rejects_expired_certificate_when_time_enforced(tmp_path):
    now = datetime.now(UTC)
    _cert, pem = _client_cert(
        not_before=now - timedelta(days=2),
        not_after=now - timedelta(days=1),
    )
    svc = CertificateAuthService(config_dir=str(tmp_path), allow_profile_login=True)

    principal, error = svc.authenticate_headers(_headers(pem))

    assert principal is None
    assert "expired" in error


def test_requires_local_allowlist_by_default(tmp_path):
    _cert, pem = _client_cert()
    svc = CertificateAuthService(config_dir=str(tmp_path))

    principal, error = svc.authenticate_headers(_headers(pem))

    assert principal is None
    assert "locally allowed" in error


def test_trusted_ca_path_accepts_ca_signed_certificate(tmp_path):
    ca_key, ca_cert, ca_pem = _ca_cert()
    _cert, pem = _client_cert_signed_by_ca(ca_key, ca_cert)
    ca_path = tmp_path / "home-monitor-provisioning-ca.crt"
    ca_path.write_text(ca_pem, encoding="utf-8")
    svc = CertificateAuthService(
        config_dir=str(tmp_path),
        allow_profile_login=True,
        trust_ca_path=str(ca_path),
    )

    principal, error = svc.authenticate_headers(_headers(pem))

    assert error == ""
    assert principal is not None
    assert principal.username == "service-laptop-01"


def test_trusted_ca_path_rejects_other_issuer(tmp_path):
    _ca_key, _ca_certificate, ca_pem = _ca_cert()
    other_key, other_ca, _other_pem = _ca_cert(common_name="Other CA")
    _cert, pem = _client_cert_signed_by_ca(other_key, other_ca)
    ca_path = tmp_path / "home-monitor-provisioning-ca.crt"
    ca_path.write_text(ca_pem, encoding="utf-8")
    svc = CertificateAuthService(
        config_dir=str(tmp_path),
        allow_profile_login=True,
        trust_ca_path=str(ca_path),
    )

    principal, error = svc.authenticate_headers(_headers(pem))

    assert principal is None
    assert "trusted CA" in error


def test_profile_login_generates_id_from_full_sha256_fingerprint(tmp_path):
    cert, pem = _client_cert()
    svc = CertificateAuthService(config_dir=str(tmp_path), allow_profile_login=True)

    principal, error = svc.authenticate_headers(_headers(pem))

    from monitor.services.certificate_auth_service import _fingerprint

    expected_digest = _fingerprint(cert).removeprefix("SHA256:").replace(":", "")
    assert error == ""
    assert principal is not None
    assert principal.user_id == f"cert-{expected_digest[:16].lower()}"


def test_maps_allowed_owner_admin_certificate(tmp_path):
    cert, pem = _client_cert()
    svc = CertificateAuthService(config_dir=str(tmp_path))
    fingerprint = svc.authenticate_headers(
        _headers(
            pem
        ),  # rejected first, but fingerprint can be computed via allowlist below
    )[0]
    assert fingerprint is None

    from monitor.services.certificate_auth_service import _fingerprint

    cert_fingerprint = _fingerprint(cert)
    (tmp_path / "cert-users.json").write_text(
        json.dumps(
            {
                "users": [
                    {
                        "id": "cert-user-vinu",
                        "display_name": "Vinu laptop",
                        "cert_fingerprint": cert_fingerprint,
                        "role": "admin",
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    principal, error = svc.authenticate_headers(_headers(pem))

    assert error == ""
    assert principal is not None
    assert principal.user_id == "cert-user-vinu"
    assert principal.username == "Vinu laptop"
    assert principal.role == "admin"
    assert principal.profile == "owner-admin"


def test_denylist_blocks_otherwise_valid_certificate(tmp_path):
    cert, pem = _client_cert()
    from monitor.services.certificate_auth_service import _fingerprint

    cert_fingerprint = _fingerprint(cert)
    (tmp_path / "cert-denylist.json").write_text(
        json.dumps({"fingerprints": [cert_fingerprint]}),
        encoding="utf-8",
    )
    svc = CertificateAuthService(config_dir=str(tmp_path), allow_profile_login=True)

    principal, error = svc.authenticate_headers(_headers(pem))

    assert principal is None
    assert "denied" in error
