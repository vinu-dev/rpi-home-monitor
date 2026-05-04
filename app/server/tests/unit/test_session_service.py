# REQ: SWR-001, SWR-009; RISK: RISK-002, RISK-020; SEC: SC-001, SC-020; TEST: TC-004, TC-049
"""Unit coverage for the server-side session inventory service."""

from datetime import UTC, datetime, timedelta

from monitor.models import User


def _user(username: str = "admin", role: str = "admin") -> User:
    return User(
        id=f"user-{username}",
        username=username,
        password_hash="hash",
        role=role,
        created_at="2026-01-01T00:00:00Z",
    )


def test_issue_persists_truncated_user_agent(app):
    record = app.session_service.issue(
        _user(),
        source_ip="192.168.1.10",
        user_agent="X" * 600,
    )

    stored = app.store.get_session(record.id)
    assert stored is not None
    assert stored.source_ip == "192.168.1.10"
    assert len(stored.user_agent.encode("utf-8")) == 512
    assert stored.expires_at > stored.created_at


def test_touch_flushes_last_active_on_interval(app, monkeypatch):
    clock = {"now": 1000.0}

    def fake_time():
        return clock["now"]

    monkeypatch.setattr("monitor.services.session_service.time.time", fake_time)

    record = app.session_service.issue(_user(), source_ip="10.0.0.4", user_agent="UA")
    assert app.store.get_session(record.id).last_active == 1000.0

    clock["now"] = 1005.0
    assert app.session_service.touch(record.id) is True
    assert app.store.get_session(record.id).last_active == 1000.0

    clock["now"] = 1011.0
    assert app.session_service.touch(record.id) is True
    assert app.store.get_session(record.id).last_active == 1011.0


def test_list_sessions_marks_locked_out_users_for_admin_scope(app):
    admin = _user("admin", "admin")
    viewer = _user("viewer", "viewer")
    viewer.locked_until = (datetime.now(UTC) + timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    app.store.save_user(admin)
    app.store.save_user(viewer)
    admin_row = app.session_service.issue(
        admin, source_ip="10.0.0.1", user_agent="Firefox/126 Linux"
    )
    app.session_service.issue(
        viewer, source_ip="10.0.0.2", user_agent="Chrome/125 Windows NT 10.0"
    )

    rows = app.session_service.list_sessions(
        requesting_user_id=admin.id,
        current_session_id=admin_row.id,
        include_all=True,
    )

    by_user = {row["username"]: row for row in rows}
    assert by_user["admin"]["is_current"] is True
    assert by_user["viewer"]["is_locked_out"] is True
    assert by_user["viewer"]["user_agent_parsed"]["browser"].startswith("Chrome")


def test_expire_discards_session_and_logs_event(app):
    user = _user("viewer", "viewer")
    record = app.session_service.issue(user, source_ip="10.0.0.8", user_agent="UA")

    app.session_service.expire(
        record.id,
        fallback_user=user.username,
        fallback_ip="10.0.0.8",
        detail="idle timeout",
    )

    assert app.store.get_session(record.id) is None
    events = app.audit.get_events(limit=5, event_type="SESSION_EXPIRED")
    assert events
    assert "idle timeout" in events[0]["detail"]
