# REQ: SWR-033, SWR-041; RISK: RISK-016; SEC: SC-015; TEST: TC-031
"""Tests for the notifications API (ADR-0027)."""

from unittest.mock import MagicMock


class TestPendingEndpoint:
    def test_requires_auth(self, client):
        response = client.get("/api/v1/notifications/pending")
        assert response.status_code == 401

    def test_returns_pending_list(self, app, logged_in_client):
        app.notification_policy = MagicMock()
        app.notification_policy.select_for_user.return_value = [
            {
                "alert_id": "motion:m1",
                "camera_id": "cam-x",
                "camera_name": "Front Door",
                "started_at": "2026-05-02T08:00:00Z",
                "duration_seconds": 5,
                "snapshot_url": None,
                "deep_link": "/events/m1",
            }
        ]
        client = logged_in_client()
        response = client.get("/api/v1/notifications/pending")
        assert response.status_code == 200
        data = response.get_json()
        assert data["count"] == 1
        assert data["alerts"][0]["camera_name"] == "Front Door"

    def test_passes_since_and_limit(self, app, logged_in_client):
        app.notification_policy = MagicMock()
        app.notification_policy.select_for_user.return_value = []
        client = logged_in_client()
        client.get("/api/v1/notifications/pending?since=2026-05-02T07:00:00Z&limit=10")
        kwargs = app.notification_policy.select_for_user.call_args.kwargs
        assert kwargs["since"] == "2026-05-02T07:00:00Z"
        assert kwargs["limit"] == 10

    def test_limit_clamped(self, app, logged_in_client):
        app.notification_policy = MagicMock()
        app.notification_policy.select_for_user.return_value = []
        client = logged_in_client()
        client.get("/api/v1/notifications/pending?limit=9999")
        assert app.notification_policy.select_for_user.call_args.kwargs["limit"] == 100
        client.get("/api/v1/notifications/pending?limit=0")
        assert app.notification_policy.select_for_user.call_args.kwargs["limit"] == 1


class TestSeenEndpoint:
    def test_requires_auth(self, client):
        response = client.post("/api/v1/notifications/seen")
        assert response.status_code == 401

    def test_marks_seen(self, app, logged_in_client):
        app.notification_policy = MagicMock()
        app.notification_policy.mark_seen.return_value = 2
        client = logged_in_client()
        response = client.post(
            "/api/v1/notifications/seen",
            json={"alert_ids": ["motion:m1", "motion:m2"]},
        )
        assert response.status_code == 200
        assert response.get_json() == {"marked": 2}

    def test_rejects_non_list_alert_ids(self, app, logged_in_client):
        app.notification_policy = MagicMock()
        client = logged_in_client()
        response = client.post(
            "/api/v1/notifications/seen", json={"alert_ids": "motion:m1"}
        )
        assert response.status_code == 400


class TestPrefsEndpoints:
    def test_get_requires_auth(self, client):
        response = client.get("/api/v1/notifications/prefs")
        assert response.status_code == 401

    def test_get_returns_prefs(self, app, logged_in_client):
        app.notification_policy = MagicMock()
        app.notification_policy.get_prefs.return_value = {
            "enabled": True,
            "cameras": {},
            "notification_schedule": [],
        }
        client = logged_in_client()
        response = client.get("/api/v1/notifications/prefs")
        assert response.status_code == 200
        assert response.get_json() == {
            "prefs": {"enabled": True, "cameras": {}, "notification_schedule": []}
        }

    def test_put_validates_body(self, app, logged_in_client):
        app.notification_policy = MagicMock()
        app.notification_policy.update_prefs.return_value = (
            {},
            "enabled must be a boolean",
        )
        client = logged_in_client()
        response = client.put("/api/v1/notifications/prefs", json={"enabled": "yes"})
        assert response.status_code == 400
        assert "boolean" in response.get_json()["error"]

    def test_put_returns_updated_prefs(self, app, logged_in_client):
        app.notification_policy = MagicMock()
        app.notification_policy.update_prefs.return_value = (
            {"enabled": True, "cameras": {}, "notification_schedule": []},
            "",
        )
        client = logged_in_client()
        response = client.put("/api/v1/notifications/prefs", json={"enabled": True})
        assert response.status_code == 200
        assert response.get_json()["prefs"]["enabled"] is True
        assert response.get_json()["prefs"]["notification_schedule"] == []
