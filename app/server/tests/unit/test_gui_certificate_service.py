from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from monitor.services.gui_certificate_service import GuiCertificateService


def _ca(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Local CA")])
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
    ca_path = tmp_path / "ca.crt"
    ca_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert, ca_path


def _sign_csr(csr_pem, ca_key, ca_cert, *, server_auth=True):
    csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value,
            critical=False,
        )
    )
    if server_auth:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
    return builder.sign(ca_key, hashes.SHA256()).public_bytes(
        serialization.Encoding.PEM
    ).decode("ascii")


def test_create_csr_keeps_private_key_on_rpi(tmp_path):
    _, _, ca_path = _ca(tmp_path)
    svc = GuiCertificateService(
        certs_dir=str(tmp_path / "certs"),
        trust_ca_path=str(ca_path),
        nginx_reload=False,
    )

    csr_pem, filename = svc.create_csr(
        common_name="rpi-test.local",
        sans=["rpi-test.local", "192.168.4.1"],
    )

    assert filename == "gui-server.csr"
    assert "BEGIN CERTIFICATE REQUEST" in csr_pem
    assert (tmp_path / "certs" / "gui-server.key").exists()
    assert not (tmp_path / "certs" / "server.key").exists()


def test_install_accepts_matching_local_ca_server_certificate(tmp_path):
    ca_key, ca_cert, ca_path = _ca(tmp_path)
    svc = GuiCertificateService(
        certs_dir=str(tmp_path / "certs"),
        trust_ca_path=str(ca_path),
        nginx_reload=False,
    )
    csr_pem, _ = svc.create_csr(
        common_name="rpi-test.local",
        sans=["rpi-test.local", "192.168.4.1"],
    )
    cert_pem = _sign_csr(csr_pem, ca_key, ca_cert)

    ok, error = svc.install_certificate(cert_pem)

    assert ok is True
    assert error == ""
    assert (tmp_path / "certs" / "gui-server.crt").read_text(encoding="utf-8")


def test_install_rejects_certificate_without_server_auth(tmp_path):
    ca_key, ca_cert, ca_path = _ca(tmp_path)
    svc = GuiCertificateService(
        certs_dir=str(tmp_path / "certs"),
        trust_ca_path=str(ca_path),
        nginx_reload=False,
    )
    csr_pem, _ = svc.create_csr(
        common_name="rpi-test.local",
        sans=["rpi-test.local", "192.168.4.1"],
    )
    cert_pem = _sign_csr(csr_pem, ca_key, ca_cert, server_auth=False)

    ok, error = svc.install_certificate(cert_pem)

    assert ok is False
    assert "extended key usage" in error
