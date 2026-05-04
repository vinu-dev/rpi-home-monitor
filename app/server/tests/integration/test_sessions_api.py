# REQ: SWR-001, SWR-002; RISK: RISK-002, RISK-020; SEC: SC-001, SC-020; TEST: TC-004, TC-011
"""Integration tests for the active-session enumeration and revoke APIs."""

import time
from datetime import UTC, datetime, timedelta

from monitor.auth import hash_password
from monitor.models import User


def _create_user(app, username: str, *, role: str = "viewer", password: str = "pass"):
    user = User(
        id=f"user-{username}",
        username=username,
        password_hash=hash_password(password),
        role=role,
        created_at="2026-01-01T00:00:00Z",
    )
    app.store.save_user(user)
    return user


def _login_client(app, username: str, *, role: str = "viewer", password: str = "pass"):
    user = app.store.get_user_by_username(username) or _create_user(
        app, username, role=role, password=password
    )
    client = app.test_client()
    response = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": password},
    )
    assert response.status_code == 200
    client.environ_base["HTTP_X_CSRF_TOKEN"] = response.get_json()["csrf_token"]
    return client


def _session_snapshot(client):
    with client.session_transaction() as sess:
        return dict(sess)


def test_get_sessions_returns_current_row_first(app):
    _create_user(app, "admin", role="admin")
    current = _login_client(app, "admin", role="admin")
    other = _login_client(app, "admin", role="admin")
    current_sid = _session_snapshot(current)["sid"]
    other_sid = _session_snapshot(other)["sid"]

    response = current.get("/api/v1/sessions")
    assert response.status_code == 200
    rows = response.get_json()["sessions"]
    assert [row["id"] for row in rows] == [current_sid, other_sid]
    assert rows[0]["is_current"] is True
    assert rows[1]["is_current"] is False


def test_non_admin_scope_all_is_ignored(app):
    _create_user(app, "viewer", role="viewer")
    _create_user(app, "admin", role="admin")
    viewer = _login_client(app, "viewer", role="viewer")
    _login_client(app, "admin", role="admin")

    response = viewer.get("/api/v1/sessions?scope=all")
    assert response.status_code == 200
    rows = response.get_json()["sessions"]
    assert rows
    assert all(row["username"] == "viewer" for row in rows)


def test_admin_scope_all_lists_other_users_and_lockout_state(app):
    _create_user(app, "admin", role="admin")
    admin_client = _login_client(app, "admin", role="admin")
    _create_user(app, "viewer", role="viewer")
    _login_client(app, "viewer", role="viewer")
    viewer = app.store.get_user_by_username("viewer")
    viewer.locked_until = (datetime.now(UTC) + timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    app.store.save_user(viewer)

    response = admin_client.get("/api/v1/sessions?scope=all")
    assert response.status_code == 200
    rows = response.get_json()["sessions"]
    usernames = {row["username"] for row in rows}
    assert {"admin", "viewer"} <= usernames
    viewer_rows = [row for row in rows if row["username"] == "viewer"]
    assert viewer_rows[0]["is_locked_out"] is True


def test_revoke_other_device_invalidates_that_cookie(app):
    _create_user(app, "admin", role="admin")
    current = _login_client(app, "admin", role="admin")
    other = _login_client(app, "admin", role="admin")
    other_sid = _session_snapshot(other)["sid"]

    response = current.delete(f"/api/v1/sessions/{other_sid}")
    assert response.status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401
    assert current.get("/api/v1/auth/me").status_code == 200


def test_non_admin_cannot_revoke_someone_elses_session(app):
    _create_user(app, "viewer1", role="viewer")
    _create_user(app, "viewer2", role="viewer")
    caller = _login_client(app, "viewer1", role="viewer")
    target = _login_client(app, "viewer2", role="viewer")
    target_sid = _session_snapshot(target)["sid"]

    response = caller.delete(f"/api/v1/sessions/{target_sid}")
    assert response.status_code == 404
    assert target.get("/api/v1/auth/me").status_code == 200


def test_revoke_others_preserves_current_session(app):
    _create_user(app, "admin", role="admin")
    current = _login_client(app, "admin", role="admin")
    other_one = _login_client(app, "admin", role="admin")
    other_two = _login_client(app, "admin", role="admin")

    response = current.delete("/api/v1/sessions/others")
    assert response.status_code == 200
    assert response.get_json()["revoked_count"] == 2
    assert current.get("/api/v1/auth/me").status_code == 200
    assert other_one.get("/api/v1/auth/me").status_code == 401
    assert other_two.get("/api/v1/auth/me").status_code == 401


def test_legacy_session_is_listed_and_can_be_cleared(app, client):
    _create_user(app, "legacy", role="viewer")
    with client.session_transaction() as sess:
        now = time.time()
        sess["user_id"] = "user-legacy"
        sess["username"] = "legacy"
        sess["role"] = "viewer"
        sess["created_at"] = now
        sess["last_active"] = now
        sess["csrf_token"] = "legacy-token"
    client.environ_base["HTTP_X_CSRF_TOKEN"] = "legacy-token"

    response = client.get("/api/v1/sessions")
    assert response.status_code == 200
    rows = response.get_json()["sessions"]
    assert len(rows) == 1
    assert rows[0]["id"] == "legacy-current"
    assert rows[0]["is_legacy"] is True
    assert rows[0]["is_current"] is True

    cleared = client.delete("/api/v1/sessions/legacy-current")
    assert cleared.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
