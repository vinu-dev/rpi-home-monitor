# REQ: SWR-001, SWR-034; RISK: RISK-002, RISK-019; SEC: SC-001, SC-017; TEST: TC-004, TC-032
"""Security tests for opt-in client certificate authentication."""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from monitor import create_app
from monitor.auth import hash_password
from monitor.models import User
from monitor.services.certificate_auth_service import (
    CERT_HEADER,
    SERIAL_HEADER,
    VERIFY_HEADER,
)


def _make_app(data_dir, *, auth_mode="password", allow_profile_login=False):
    return create_app(
        config={
            "TESTING": True,
            "DATA_DIR": str(data_dir),
            "RECORDINGS_DIR": str(data_dir / "recordings"),
            "LIVE_DIR": str(data_dir / "live"),
            "CONFIG_DIR": str(data_dir / "config"),
            "CERTS_DIR": str(data_dir / "certs"),
            "SECRET_KEY": "test-secret-key-do-not-use-in-prod",
            "CLIP_DURATION_SECONDS": 180,
            "STORAGE_THRESHOLD_PERCENT": 90,
            "SESSION_TIMEOUT_MINUTES": 30,
            "SESSION_COOKIE_SECURE": False,
            "AUTH_MODE": auth_mode,
            "CERT_AUTH_ALLOW_PROFILE_LOGIN": allow_profile_login,
            "CERT_AUTH_TRUST_CA_PATH": "",
        }
    )


def _client_cert(*, common_name="owner-laptop", profile="owner-admin", serial=1001):
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
        .issuer_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, "Home Monitor Test CA"),
                ]
            )
        )
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"urn:home-monitor:profile:{profile}")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _cert_headers(pem, *, verify="SUCCESS", serial="3E9"):
    return {
        VERIFY_HEADER: verify,
        CERT_HEADER: quote(pem),
        SERIAL_HEADER: serial,
    }


def test_password_mode_remains_default(app, client):
    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash=hash_password("pass"),
            role="admin",
            created_at="2026-01-01T00:00:00Z",
        )
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "pass"},
    )

    assert response.status_code == 200
    assert response.get_json()["user"]["role"] == "admin"


def test_certificate_mode_disables_password_login(data_dir):
    app = _make_app(data_dir, auth_mode="certificate")
    client = app.test_client()
    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash=hash_password("pass"),
            role="admin",
            created_at="2026-01-01T00:00:00Z",
        )
    )

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "pass"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "Password login is disabled"


def test_certificate_mode_login_page_hides_password_form(data_dir):
    app = _make_app(data_dir, auth_mode="certificate")
    client = app.test_client()
    (data_dir / ".setup-done").write_text("1", encoding="utf-8")

    response = client.get("/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<input type="text" id="login-username"' not in html
    assert '<input type="password" id="login-password"' not in html
    assert "Admin Certificate Login" in html
    assert "Password sign-in is disabled on this device." in html
    assert "certificateAuthPort = '9443'" in html
    assert "redirectToCertificatePort" in html
    assert "cert_login" in html
    assert "wantsCertificateLogin()" in html


def test_certificate_mode_logged_out_page_is_stable(data_dir):
    app = _make_app(data_dir, auth_mode="certificate", allow_profile_login=True)
    client = app.test_client()
    (data_dir / ".setup-done").write_text("1", encoding="utf-8")

    response = client.get("/login?logged_out=1", headers=_cert_headers(_client_cert()))

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Signed out." in html
    assert "Admin Certificate Login" in html
    assert "wantsCertificateLogin()" in html


def test_certificate_mode_protected_view_does_not_auto_login(data_dir):
    app = _make_app(data_dir, auth_mode="certificate", allow_profile_login=True)
    client = app.test_client()
    (data_dir / ".setup-done").write_text("1", encoding="utf-8")

    response = client.get("/dashboard", headers=_cert_headers(_client_cert()))

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_password_mode_login_page_keeps_password_form(data_dir):
    app = _make_app(data_dir, auth_mode="password")
    client = app.test_client()
    (data_dir / ".setup-done").write_text("1", encoding="utf-8")

    response = client.get("/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "login-username" in html
    assert "login-password" in html
    assert "Admin Certificate Login" not in html


def test_mixed_mode_login_page_offers_password_and_admin_certificate(data_dir):
    app = _make_app(data_dir, auth_mode="mixed", allow_profile_login=True)
    client = app.test_client()
    (data_dir / ".setup-done").write_text("1", encoding="utf-8")

    response = client.get("/login")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "login-username" in html
    assert "login-password" in html
    assert "Admin Certificate Login" in html
    assert "Use this only for setup, recovery, and user management." in html
    assert "Password sign-in is disabled on this device." not in html
    assert "normalPortUrl('/dashboard')" in html


def test_certificate_mode_creates_cert_backed_session(data_dir):
    app = _make_app(
        data_dir,
        auth_mode="certificate",
        allow_profile_login=True,
    )
    client = app.test_client()
    pem = _client_cert()

    response = client.post("/api/v1/auth/cert/session", headers=_cert_headers(pem))

    assert response.status_code == 200
    body = response.get_json()
    assert body["auth_method"] == "client_certificate"
    assert body["user"]["username"] == "owner-laptop"
    assert body["user"]["role"] == "admin"
    assert body["certificate"]["profile"] == "owner-admin"

    me = client.get("/api/v1/auth/cert/me")
    assert me.status_code == 200
    assert me.get_json()["auth_method"] == "client_certificate"


def test_certificate_session_can_read_totp_status_for_settings_page(data_dir):
    app = _make_app(
        data_dir,
        auth_mode="certificate",
        allow_profile_login=True,
    )
    client = app.test_client()
    pem = _client_cert()
    login = client.post("/api/v1/auth/cert/session", headers=_cert_headers(pem))
    assert login.status_code == 200

    response = client.get("/api/v1/auth/totp/status")

    assert response.status_code == 200
    assert response.get_json() == {
        "available": False,
        "enabled": False,
        "reason": "certificate_session",
        "recovery_codes_remaining": 0,
    }


def test_certificate_session_rejects_unverified_header(data_dir):
    app = _make_app(
        data_dir,
        auth_mode="certificate",
        allow_profile_login=True,
    )
    client = app.test_client()
    pem = _client_cert()

    response = client.post(
        "/api/v1/auth/cert/session",
        headers=_cert_headers(pem, verify="FAILED"),
    )

    assert response.status_code == 401
    assert "not verified" in response.get_json()["error"]


def test_certificate_endpoint_is_disabled_in_password_mode(app, client):
    pem = _client_cert()

    response = client.post("/api/v1/auth/cert/session", headers=_cert_headers(pem))

    assert response.status_code == 404
    assert "not enabled" in response.get_json()["error"]


def test_string_zero_does_not_enable_profile_login(data_dir):
    app = _make_app(
        data_dir,
        auth_mode="certificate",
        allow_profile_login="0",
    )
    client = app.test_client()
    pem = _client_cert()

    response = client.post("/api/v1/auth/cert/session", headers=_cert_headers(pem))

    assert response.status_code == 401
    assert "locally allowed" in response.get_json()["error"]


def test_mixed_mode_keeps_password_login_and_accepts_cert(data_dir):
    app = _make_app(data_dir, auth_mode="mixed", allow_profile_login=True)
    client = app.test_client()
    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash=hash_password("pass"),
            role="admin",
            created_at="2026-01-01T00:00:00Z",
        )
    )

    password_response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "pass"},
    )
    assert password_response.status_code == 200

    client.post("/api/v1/auth/logout")
    cert_response = client.post(
        "/api/v1/auth/cert/session",
        headers=_cert_headers(
            _client_cert(common_name="viewer-phone", profile="viewer")
        ),
    )

    assert cert_response.status_code == 200
    assert cert_response.get_json()["user"]["role"] == "viewer"
