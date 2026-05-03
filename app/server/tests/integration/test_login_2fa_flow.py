# REQ: SWR-238-A, SWR-238-B, SWR-238-C, SWR-238-D
# RISK: RISK-238-1, RISK-238-2, RISK-238-3
# SEC: SEC-238-A, SEC-238-C
# TEST: TC-238-AC-1, TC-238-AC-3, TC-238-AC-4, TC-238-AC-7, TC-238-AC-9, TC-238-AC-10, TC-238-AC-11
"""End-to-end login flow tests covering the 2FA challenge step."""

import pyotp
import pytest

from monitor.auth import _login_attempts, hash_password
from monitor.models import User


@pytest.fixture(autouse=True)
def reset_rate_limits():
    _login_attempts.clear()
    yield
    _login_attempts.clear()


def _make_user(app, username="alice", with_totp=False, role="viewer"):
    secret = pyotp.random_base32() if with_totp else ""
    user = User(
        id=f"user-{username}",
        username=username,
        password_hash=hash_password("password1234"),
        role=role,
        created_at="2026-01-01T00:00:00Z",
        totp_secret=secret,
        totp_enabled=with_totp,
    )
    app.store.save_user(user)
    return user, secret


def test_login_no_2fa_returns_session_directly(app, client):
    _make_user(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert "csrf_token" in body
    assert body["user"]["username"] == "alice"
    assert "challenge" not in body


def test_login_with_2fa_returns_challenge_token(app, client):
    _make_user(app, with_totp=True)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["challenge"] == "totp"
    assert body["challenge_token"]
    # Session must NOT yet be a logged-in session.
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401


def test_correct_totp_code_completes_login(app, client):
    _user, secret = _make_user(app, with_totp=True)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
    )
    token = resp.get_json()["challenge_token"]
    code = pyotp.TOTP(secret).now()
    resp2 = client.post(
        "/api/v1/auth/totp/verify",
        json={"challenge_token": token, "code": code},
    )
    assert resp2.status_code == 200
    body = resp2.get_json()
    assert body["user"]["username"] == "alice"
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200


def test_wrong_totp_code_increments_failed_logins(app, client):
    _user, _secret = _make_user(app, with_totp=True)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
    )
    token = resp.get_json()["challenge_token"]
    bad = client.post(
        "/api/v1/auth/totp/verify",
        json={"challenge_token": token, "code": "000000"},
    )
    assert bad.status_code == 401
    user = app.store.get_user_by_username("alice")
    assert user.failed_logins == 1


def test_replay_of_same_totp_code_rejected(app, client):
    _user, secret = _make_user(app, with_totp=True)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
    )
    token = resp.get_json()["challenge_token"]
    code = pyotp.TOTP(secret).now()
    first = client.post(
        "/api/v1/auth/totp/verify",
        json={"challenge_token": token, "code": code},
    )
    assert first.status_code == 200

    # Log out, start a fresh password+challenge, then try to replay
    # the same code from the previous step.
    client.post("/api/v1/auth/logout")
    resp2 = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
    )
    token2 = resp2.get_json()["challenge_token"]
    replay = client.post(
        "/api/v1/auth/totp/verify",
        json={"challenge_token": token2, "code": code},
    )
    assert replay.status_code == 401


def test_expired_challenge_token_rejected(app, client):
    user, secret = _make_user(app, with_totp=True)
    # Manually mint a past-dated token so we don't have to sleep
    # CHALLENGE_TTL_SECONDS in the test.
    expired = app.totp_service.issue_challenge_token(user.id, at=0)
    code = pyotp.TOTP(secret).now()
    resp = client.post(
        "/api/v1/auth/totp/verify",
        json={"challenge_token": expired, "code": code},
    )
    assert resp.status_code == 401


def test_recovery_code_consumes_one_slot(app, client):
    user, _secret = _make_user(app, with_totp=True)
    plaintexts, hashes = app.totp_service.generate_recovery_codes(count=3)
    user.recovery_code_hashes = hashes
    app.store.save_user(user)

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
    )
    token = resp.get_json()["challenge_token"]
    rc_resp = client.post(
        "/api/v1/auth/totp/verify",
        json={"challenge_token": token, "code": plaintexts[0], "recovery": True},
    )
    assert rc_resp.status_code == 200, rc_resp.get_data(as_text=True)
    refreshed = app.store.get_user_by_username("alice")
    assert len(refreshed.recovery_code_hashes) == 2


def test_recovery_code_replay_rejected(app, client):
    user, _ = _make_user(app, with_totp=True)
    plaintexts, hashes = app.totp_service.generate_recovery_codes(count=2)
    user.recovery_code_hashes = hashes
    app.store.save_user(user)

    # Use a code once.
    r = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "password1234"}
    )
    token = r.get_json()["challenge_token"]
    ok = client.post(
        "/api/v1/auth/totp/verify",
        json={"challenge_token": token, "code": plaintexts[0], "recovery": True},
    )
    assert ok.status_code == 200
    client.post("/api/v1/auth/logout")
    # Replay the same recovery code in a fresh login flow.
    r2 = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "password1234"}
    )
    token2 = r2.get_json()["challenge_token"]
    bad = client.post(
        "/api/v1/auth/totp/verify",
        json={"challenge_token": token2, "code": plaintexts[0], "recovery": True},
    )
    assert bad.status_code == 401


def test_remote_policy_refuses_non_enrolled_user(app, client, monkeypatch):
    """AC-11: with require_2fa_for_remote ON, a non-enrolled user
    coming in via a Tailscale-Funnel-classified IP cannot complete a
    remote login."""
    _make_user(app)  # alice is NOT enrolled
    settings = app.store.get_settings()
    settings.require_2fa_for_remote = True
    app.store.save_settings(settings)

    # Force the request_origin classifier to mark this as remote by
    # spoofing the source IP at the WSGI layer.
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
        environ_base={"REMOTE_ADDR": "100.64.1.5"},  # in CGNAT range
    )
    assert resp.status_code == 403
    body = resp.get_json()
    assert body.get("remote_2fa_enrollment_required") is True


def test_remote_policy_off_allows_non_enrolled_user(app, client):
    """When the policy is off, a non-enrolled user logs in normally
    even from a Tailscale IP."""
    _make_user(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
        environ_base={"REMOTE_ADDR": "100.64.1.5"},
    )
    assert resp.status_code == 200
    assert "challenge" not in resp.get_json()


def test_remote_policy_does_not_apply_to_lan(app, client):
    """When the policy is on but the request is LAN, a non-enrolled
    user logs in unhindered."""
    _make_user(app)
    settings = app.store.get_settings()
    settings.require_2fa_for_remote = True
    app.store.save_settings(settings)

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password1234"},
        environ_base={"REMOTE_ADDR": "192.168.1.42"},
    )
    assert resp.status_code == 200
    assert "challenge" not in resp.get_json()
