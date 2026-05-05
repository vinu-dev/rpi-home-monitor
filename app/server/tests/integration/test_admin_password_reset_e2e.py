# REQ: SWR-023, SWR-099; RISK: RISK-011, RISK-099; SEC: SC-011, SC-099; TEST: TC-022, TC-099
"""End-to-end coverage for admin-assisted password reset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from monitor.auth import hash_password
from monitor.models import User


def _create_user(
    app,
    username: str,
    *,
    role: str = "viewer",
    password: str,
    **overrides,
):
    user = User(
        id=f"user-{username}",
        username=username,
        password_hash=hash_password(password),
        role=role,
        created_at="2026-01-01T00:00:00Z",
        **overrides,
    )
    app.store.save_user(user)
    return user


def _login_client(
    app,
    username: str,
    password: str,
    *,
    role: str = "viewer",
    base_url: str | None = None,
):
    if app.store.get_user_by_username(username) is None:
        _create_user(app, username, role=role, password=password)

    client = app.test_client()
    request_kwargs = {"json": {"username": username, "password": password}}
    if base_url is not None:
        request_kwargs["base_url"] = base_url
    response = client.post("/api/v1/auth/login", **request_kwargs)
    assert response.status_code == 200, response.get_data(as_text=True)
    client.environ_base["HTTP_X_CSRF_TOKEN"] = response.get_json()["csrf_token"]
    return client, response


def _session_snapshot(client):
    with client.session_transaction() as sess:
        return dict(sess)


def test_admin_reset_then_target_login_blocks_until_change(app):
    admin = _create_user(app, "admin", role="admin", password="admin-pass-12345")
    target = _create_user(app, "viewer1", password="viewer-old-pass-123")
    admin_client, _ = _login_client(
        app, admin.username, "admin-pass-12345", role="admin"
    )

    reset = admin_client.put(
        f"/api/v1/users/{target.id}/password",
        json={"new_password": "TempPassword123!", "force_change": True},
    )
    assert reset.status_code == 200, reset.get_data(as_text=True)
    assert app.store.get_user(target.id).must_change_password is True

    target_client, login = _login_client(app, target.username, "TempPassword123!")
    assert login.get_json()["must_change_password"] is True
    assert _session_snapshot(target_client)["must_change_password"] is True

    assert target_client.get("/api/v1/auth/me").status_code == 200

    blocked = target_client.get("/api/v1/cameras")
    assert blocked.status_code == 403
    assert blocked.get_json()["must_change_password"] is True

    changed = target_client.put(
        f"/api/v1/users/{target.id}/password",
        json={"new_password": "ViewerFinalPass123!"},
    )
    assert changed.status_code == 200, changed.get_data(as_text=True)
    assert _session_snapshot(target_client)["must_change_password"] is False
    assert app.store.get_user(target.id).must_change_password is False

    assert target_client.get("/api/v1/cameras").status_code == 200


def test_post_change_request_unblocks_session_immediately(app):
    admin = _create_user(app, "admin", role="admin", password="admin-pass-12345")
    target = _create_user(app, "viewer1", password="viewer-old-pass-123")
    admin_client, _ = _login_client(
        app, admin.username, "admin-pass-12345", role="admin"
    )

    reset = admin_client.put(
        f"/api/v1/users/{target.id}/password",
        json={"new_password": "TempPassword123!", "force_change": True},
    )
    assert reset.status_code == 200, reset.get_data(as_text=True)

    target_client, _ = _login_client(app, target.username, "TempPassword123!")
    sid_before = _session_snapshot(target_client)["sid"]
    assert target_client.get("/api/v1/cameras").status_code == 403

    changed = target_client.put(
        f"/api/v1/users/{target.id}/password",
        json={"new_password": "ViewerFinalPass123!"},
    )
    assert changed.status_code == 200, changed.get_data(as_text=True)

    sid_after = _session_snapshot(target_client)["sid"]
    assert sid_after == sid_before
    assert target_client.get("/api/v1/cameras").status_code == 200


def test_admin_reset_does_not_disturb_lockout_or_session_or_totp(app):
    admin = _create_user(app, "admin", role="admin", password="admin-pass-12345")
    target = _create_user(
        app,
        "viewer1",
        password="viewer-old-pass-123",
    )
    target_client, _ = _login_client(app, target.username, "viewer-old-pass-123")
    target_sid = _session_snapshot(target_client)["sid"]

    locked_until = (datetime.now(UTC) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    target_state = app.store.get_user(target.id)
    target_state.failed_logins = 3
    target_state.locked_until = locked_until
    target_state.totp_enabled = True
    app.store.save_user(target_state)

    admin_client, _ = _login_client(
        app, admin.username, "admin-pass-12345", role="admin"
    )
    reset = admin_client.put(
        f"/api/v1/users/{target.id}/password",
        json={"new_password": "TempPassword123!", "force_change": True},
    )
    assert reset.status_code == 200, reset.get_data(as_text=True)

    assert target_client.get("/api/v1/cameras").status_code == 200
    assert target_sid in {row.id for row in app.store.get_sessions()}

    reloaded = app.store.get_user(target.id)
    assert reloaded.failed_logins == 3
    assert reloaded.locked_until == locked_until
    assert reloaded.totp_enabled is True
    assert reloaded.must_change_password is True


def test_set_cookie_flags_unchanged_on_admin_reset_response(app):
    app.config["SESSION_COOKIE_SECURE"] = True

    admin = _create_user(app, "admin", role="admin", password="admin-pass-12345")
    target = _create_user(app, "viewer1", password="viewer-old-pass-123")
    admin_client, _ = _login_client(
        app,
        admin.username,
        "admin-pass-12345",
        role="admin",
        base_url="https://localhost",
    )

    reset = admin_client.put(
        f"/api/v1/users/{target.id}/password",
        json={"new_password": "TempPassword123!", "force_change": True},
        base_url="https://localhost",
    )
    assert reset.status_code == 200, reset.get_data(as_text=True)

    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    session_cookie = next(
        (
            header
            for header in reset.headers.getlist("Set-Cookie")
            if header.startswith(f"{cookie_name}=")
        ),
        "",
    )
    assert session_cookie
    assert "; Secure;" in session_cookie
    assert "; HttpOnly;" in session_cookie
    assert "SameSite=Strict" in session_cookie
