from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from monitor.services import privileged
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
    return (
        builder.sign(ca_key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode("ascii")
    )


def _server_ca_files(tmp_path):
    certs_dir = tmp_path / "certs"
    certs_dir.mkdir(exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RPI Server CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    (certs_dir / "ca.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (certs_dir / "ca.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (certs_dir / "server.crt").write_text("SERVER CERT\n", encoding="utf-8")
    return key, cert, certs_dir


def _sign_server_ca(server_ca_cert, ca_key, ca_cert, *, key=None):
    now = datetime.now(UTC)
    public_key = key.public_key() if key is not None else server_ca_cert.public_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(server_ca_cert.subject)
        .issuer_name(ca_cert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


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


def test_install_server_ca_cross_sign_builds_browser_chain(tmp_path):
    ca_key, ca_cert, ca_path = _ca(tmp_path)
    _, server_ca_cert, certs_dir = _server_ca_files(tmp_path)
    svc = GuiCertificateService(
        certs_dir=str(certs_dir),
        trust_ca_path=str(ca_path),
        nginx_reload=False,
    )
    cert_pem = _sign_server_ca(server_ca_cert, ca_key, ca_cert)

    ok, error = svc.install_server_ca_certificate(cert_pem)

    assert ok is True
    assert error == ""
    assert (certs_dir / "server-ca-local.crt").read_text(encoding="utf-8")
    chain = (certs_dir / "server-browser-chain.crt").read_text(encoding="utf-8")
    assert "SERVER CERT" in chain
    assert "BEGIN CERTIFICATE" in chain


def test_install_server_ca_cross_sign_rejects_other_rpi_key(tmp_path):
    ca_key, ca_cert, ca_path = _ca(tmp_path)
    _, server_ca_cert, certs_dir = _server_ca_files(tmp_path)
    other_key = ec.generate_private_key(ec.SECP256R1())
    svc = GuiCertificateService(
        certs_dir=str(certs_dir),
        trust_ca_path=str(ca_path),
        nginx_reload=False,
    )
    cert_pem = _sign_server_ca(server_ca_cert, ca_key, ca_cert, key=other_key)

    ok, error = svc.install_server_ca_certificate(cert_pem)

    assert ok is False
    assert "does not match this RPI server CA public key" in error


def test_reload_uses_helper_when_socket_available_without_env(tmp_path, monkeypatch):
    svc = GuiCertificateService(certs_dir=str(tmp_path / "certs"))
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.privileged.should_use_helper",
        lambda: False,
    )
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.privileged.is_helper_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.os.name",
        "posix",
        raising=False,
    )
    monkeypatch.delenv("MONITOR_DISABLE_PRIVILEGED_HELPER", raising=False)

    with (
        patch(
            "monitor.services.gui_certificate_service.privileged.request",
            return_value={},
        ) as request,
        patch("monitor.services.gui_certificate_service.subprocess.run") as run,
    ):
        ok, error = svc.reload_nginx()

    assert ok is True
    assert error == ""
    request.assert_called_once_with("nginx.reload", timeout=20)
    run.assert_not_called()


def test_reload_respects_disable_helper_flag(tmp_path, monkeypatch):
    svc = GuiCertificateService(certs_dir=str(tmp_path / "certs"))
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.privileged.should_use_helper",
        lambda: False,
    )
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.privileged.is_helper_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.os.name",
        "posix",
        raising=False,
    )
    monkeypatch.setenv("MONITOR_DISABLE_PRIVILEGED_HELPER", "1")

    with (
        patch("monitor.services.gui_certificate_service.privileged.request") as request,
        patch("monitor.services.gui_certificate_service.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stderr = ""
        run.return_value.stdout = ""

        ok, error = svc.reload_nginx()

    assert ok is True
    assert error == ""
    request.assert_not_called()
    assert run.call_count == 2


def test_reload_unprivileged_posix_requires_helper(tmp_path, monkeypatch):
    svc = GuiCertificateService(certs_dir=str(tmp_path / "certs"))
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.privileged.should_use_helper",
        lambda: False,
    )
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.privileged.is_helper_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.os.name",
        "posix",
        raising=False,
    )
    monkeypatch.setattr(
        "monitor.services.gui_certificate_service.os.geteuid",
        lambda: 1000,
        raising=False,
    )
    monkeypatch.delenv("MONITOR_DISABLE_PRIVILEGED_HELPER", raising=False)

    with (
        patch(
            "monitor.services.gui_certificate_service.privileged.request",
            side_effect=privileged.PrivilegedHelperError("helper missing"),
        ) as request,
        patch("monitor.services.gui_certificate_service.subprocess.run") as run,
    ):
        ok, error = svc.reload_nginx()

    assert ok is False
    assert "helper missing" in error
    request.assert_called_once_with("nginx.reload", timeout=20)
    run.assert_not_called()
