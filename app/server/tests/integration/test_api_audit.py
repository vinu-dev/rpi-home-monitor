"""Tests for the audit log API (ADR-0018 Slice 3)."""

from unittest.mock import MagicMock

from monitor.auth import hash_password


def _login(app, client, role="admin"):
    """Create user with given role, login, cache CSRF."""
    from monitor.models import User

    app.store.save_user(
        User(
            id=f"user-{role}",
            username=role,
            password_hash=hash_password("pass"),
            role=role,
        )
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": role, "password": "pass"},
    )
    client.environ_base["HTTP_X_CSRF_TOKEN"] = response.get_json()["csrf_token"]


class TestAuditEventsEndpoint:
    def test_requires_auth(self, client):
        response = client.get("/api/v1/audit/events")
        assert response.status_code == 401

    def test_viewer_forbidden(self, app, client):
        _login(app, client, role="viewer")
        response = client.get("/api/v1/audit/events")
        assert response.status_code == 403

    def test_admin_returns_events_newest_first(self, app, client):
        app.audit = MagicMock()
        app.audit.get_events.return_value = [
            {
                "timestamp": "2026-04-17T22:30:00Z",
                "event": "LOGIN_OK",
                "user": "admin",
                "ip": "",
                "detail": "",
            },
            {
                "timestamp": "2026-04-17T22:00:00Z",
                "event": "OTA_COMPLETED",
                "user": "",
                "ip": "",
                "detail": "v1.2.3",
            },
        ]
        _login(app, client)
        response = client.get("/api/v1/audit/events?limit=5")
        assert response.status_code == 200
        data = response.get_json()
        assert data["count"] == 2
        assert data["events"][0]["event"] == "LOGIN_OK"
        app.audit.get_events.assert_called_once_with(limit=5, event_type="")

    def test_event_type_filter_passed_through(self, app, client):
        app.audit = MagicMock()
        app.audit.get_events.return_value = []
        _login(app, client)
        client.get("/api/v1/audit/events?event_type=LOGIN_FAILED&limit=10")
        app.audit.get_events.assert_called_once_with(
            limit=10, event_type="LOGIN_FAILED"
        )

    def test_limit_clamped_to_sane_range(self, app, client):
        app.audit = MagicMock()
        app.audit.get_events.return_value = []
        _login(app, client)
        # Over the max → clamped to 200
        client.get("/api/v1/audit/events?limit=9999")
        assert app.audit.get_events.call_args.kwargs["limit"] == 200
        # Negative / zero → clamped to 1
        client.get("/api/v1/audit/events?limit=0")
        assert app.audit.get_events.call_args.kwargs["limit"] == 1

    def test_invalid_limit_falls_back_to_default(self, app, client):
        app.audit = MagicMock()
        app.audit.get_events.return_value = []
        _login(app, client)
        client.get("/api/v1/audit/events?limit=abc")
        assert app.audit.get_events.call_args.kwargs["limit"] == 50

    def test_audit_failure_returns_empty_list(self, app, client):
        app.audit = MagicMock()
        app.audit.get_events.side_effect = RuntimeError("disk full")
        _login(app, client)
        response = client.get("/api/v1/audit/events")
        assert response.status_code == 200
        assert response.get_json() == {"events": [], "count": 0}
