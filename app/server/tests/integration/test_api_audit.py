"""Tests for the audit log API (ADR-0018 Slice 3)."""

from unittest.mock import MagicMock


class TestAuditEventsEndpoint:
    def test_requires_auth(self, client):
        response = client.get("/api/v1/audit/events")
        assert response.status_code == 401

    def test_viewer_forbidden(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.get("/api/v1/audit/events")
        assert response.status_code == 403

    def test_admin_returns_events_newest_first(self, app, logged_in_client):
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
        client = logged_in_client()
        response = client.get("/api/v1/audit/events?limit=5")
        assert response.status_code == 200
        data = response.get_json()
        assert data["count"] == 2
        assert data["events"][0]["event"] == "LOGIN_OK"
        app.audit.get_events.assert_called_once_with(limit=5, event_type="")

    def test_event_type_filter_passed_through(self, app, logged_in_client):
        app.audit = MagicMock()
        app.audit.get_events.return_value = []
        client = logged_in_client()
        client.get("/api/v1/audit/events?event_type=LOGIN_FAILED&limit=10")
        app.audit.get_events.assert_called_once_with(
            limit=10, event_type="LOGIN_FAILED"
        )

    def test_limit_clamped_to_sane_range(self, app, logged_in_client):
        app.audit = MagicMock()
        app.audit.get_events.return_value = []
        client = logged_in_client()
        # Over the max â-> clamped to 200
        client.get("/api/v1/audit/events?limit=9999")
        assert app.audit.get_events.call_args.kwargs["limit"] == 200
        # Negative / zero â-> clamped to 1
        client.get("/api/v1/audit/events?limit=0")
        assert app.audit.get_events.call_args.kwargs["limit"] == 1

    def test_invalid_limit_falls_back_to_default(self, app, logged_in_client):
        app.audit = MagicMock()
        app.audit.get_events.return_value = []
        client = logged_in_client()
        client.get("/api/v1/audit/events?limit=abc")
        assert app.audit.get_events.call_args.kwargs["limit"] == 50

    def test_audit_failure_returns_empty_list(self, app, logged_in_client):
        app.audit = MagicMock()
        app.audit.get_events.side_effect = RuntimeError("disk full")
        client = logged_in_client()
        response = client.get("/api/v1/audit/events")
        assert response.status_code == 200
        assert response.get_json() == {"events": [], "count": 0}


class TestClearAuditEventsEndpoint:
    def test_requires_auth(self, client):
        response = client.delete("/api/v1/audit/events")
        assert response.status_code == 401

    def test_viewer_forbidden(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.delete("/api/v1/audit/events")
        assert response.status_code == 403

    def test_admin_clears_log(self, app, logged_in_client):
        app.audit = MagicMock()
        client = logged_in_client()
        response = client.delete("/api/v1/audit/events")
        assert response.status_code == 200
        assert response.get_json() == {"cleared": True}
        app.audit.clear_events.assert_called_once()

    def test_clears_with_user_and_ip(self, app, logged_in_client):
        app.audit = MagicMock()
        client = logged_in_client()
        client.delete("/api/v1/audit/events")
        call_kwargs = app.audit.clear_events.call_args.kwargs
        assert call_kwargs["user"] == "admin"
        assert "ip" in call_kwargs
