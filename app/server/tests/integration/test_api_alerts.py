# REQ: SWR-017, SWR-033; RISK: RISK-005, RISK-016; SEC: SC-008, SC-015; TEST: TC-014, TC-031
"""Tests for the alert center API (ADR-0024)."""

from unittest.mock import MagicMock


class TestListAlertsEndpoint:
    def test_requires_auth(self, client):
        response = client.get("/api/v1/alerts/")
        assert response.status_code == 401

    def test_admin_returns_alerts(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.list_alerts.return_value = [
            {
                "id": "fault:cam-d8ee:boom",
                "source": "fault",
                "severity": "error",
                "timestamp": "2026-04-30T08:00:00Z",
                "subject": {"type": "camera", "id": "cam-d8ee"},
                "message": "boom",
                "deep_link": "/dashboard#cameras-section",
                "is_read": False,
                "read_at": None,
                "hint": "",
                "context": {},
            }
        ]
        app.alert_center.unread_count.return_value = 1
        client = logged_in_client()
        response = client.get("/api/v1/alerts/")
        assert response.status_code == 200
        data = response.get_json()
        assert data["count"] == 1
        assert data["unread_count"] == 1
        assert data["alerts"][0]["source"] == "fault"

    def test_viewer_can_call_endpoint(self, app, logged_in_client):
        # Permission filtering happens in the service layer; the API
        # itself only requires login. Tested here so a regression that
        # added @admin_required would fail loudly.
        app.alert_center = MagicMock()
        app.alert_center.list_alerts.return_value = []
        app.alert_center.unread_count.return_value = 0
        client = logged_in_client("viewer")
        response = client.get("/api/v1/alerts/")
        assert response.status_code == 200

    def test_filters_passed_through(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.list_alerts.return_value = []
        app.alert_center.unread_count.return_value = 0
        client = logged_in_client()
        client.get(
            "/api/v1/alerts/?source=motion&severity=warning"
            "&unread_only=1&limit=10&before=2026-04-30T00:00:00Z"
        )
        kwargs = app.alert_center.list_alerts.call_args.kwargs
        assert kwargs["source"] == "motion"
        assert kwargs["severity"] == "warning"
        assert kwargs["unread_only"] is True
        assert kwargs["limit"] == 10
        assert kwargs["before"] == "2026-04-30T00:00:00Z"

    def test_sort_importance_passed_through(self, app, logged_in_client):
        """#144 review queue — `sort=importance` reaches the service
        layer."""
        app.alert_center = MagicMock()
        app.alert_center.list_alerts.return_value = []
        app.alert_center.unread_count.return_value = 0
        client = logged_in_client()
        client.get("/api/v1/alerts/?sort=importance")
        kwargs = app.alert_center.list_alerts.call_args.kwargs
        assert kwargs["sort"] == "importance"

    def test_sort_default_is_timestamp(self, app, logged_in_client):
        """Backwards-compat — clients that don't pass sort still get
        the inbox newest-first ordering."""
        app.alert_center = MagicMock()
        app.alert_center.list_alerts.return_value = []
        app.alert_center.unread_count.return_value = 0
        client = logged_in_client()
        client.get("/api/v1/alerts/")
        kwargs = app.alert_center.list_alerts.call_args.kwargs
        assert kwargs["sort"] == "timestamp"

    def test_sort_unknown_falls_back_to_default(self, app, logged_in_client):
        """Defensive — a garbage sort= value mustn't 400 the page
        for the user; treat it as the default."""
        app.alert_center = MagicMock()
        app.alert_center.list_alerts.return_value = []
        app.alert_center.unread_count.return_value = 0
        client = logged_in_client()
        client.get("/api/v1/alerts/?sort=alphabetical")
        kwargs = app.alert_center.list_alerts.call_args.kwargs
        assert kwargs["sort"] == "timestamp"

    def test_limit_clamped(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.list_alerts.return_value = []
        app.alert_center.unread_count.return_value = 0
        client = logged_in_client()
        client.get("/api/v1/alerts/?limit=99999")
        assert app.alert_center.list_alerts.call_args.kwargs["limit"] == 200
        client.get("/api/v1/alerts/?limit=0")
        assert app.alert_center.list_alerts.call_args.kwargs["limit"] == 1
        client.get("/api/v1/alerts/?limit=abc")
        assert app.alert_center.list_alerts.call_args.kwargs["limit"] == 50

    def test_unread_count_independent_of_pagination(self, app, logged_in_client):
        # The badge consumes unread_count, not len(alerts) — even with
        # limit=1 the response surfaces the full unread total.
        app.alert_center = MagicMock()
        app.alert_center.list_alerts.return_value = [{"id": "x", "source": "fault"}]
        app.alert_center.unread_count.return_value = 17
        client = logged_in_client()
        response = client.get("/api/v1/alerts/?limit=1")
        data = response.get_json()
        assert data["count"] == 1
        assert data["unread_count"] == 17


class TestUnreadCountEndpoint:
    def test_requires_auth(self, client):
        response = client.get("/api/v1/alerts/unread-count")
        assert response.status_code == 401

    def test_returns_count(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.unread_count.return_value = 3
        client = logged_in_client()
        response = client.get("/api/v1/alerts/unread-count")
        assert response.status_code == 200
        assert response.get_json() == {"count": 3}

    def test_user_and_role_passed_to_service(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.unread_count.return_value = 0
        client = logged_in_client("viewer", username="bob")
        client.get("/api/v1/alerts/unread-count")
        kwargs = app.alert_center.unread_count.call_args.kwargs
        assert kwargs["user"] == "bob"
        assert kwargs["role"] == "viewer"


class TestMarkReadEndpoint:
    def test_requires_auth(self, client):
        response = client.post("/api/v1/alerts/audit:abcd/read")
        assert response.status_code == 401

    def test_marks_alert_read(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.mark_read.return_value = True
        client = logged_in_client()
        response = client.post("/api/v1/alerts/fault:cam-d8ee:boom/read")
        assert response.status_code == 200
        assert response.get_json() == {"ok": True}
        kwargs = app.alert_center.mark_read.call_args.kwargs
        assert kwargs["user"] == "admin"
        assert kwargs["alert_id"] == "fault:cam-d8ee:boom"

    def test_invalid_id_returns_400(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.mark_read.return_value = False
        client = logged_in_client()
        response = client.post("/api/v1/alerts/garbage/read")
        assert response.status_code == 400


class TestMarkAllReadEndpoint:
    def test_requires_auth(self, client):
        response = client.post("/api/v1/alerts/mark-all-read")
        assert response.status_code == 401

    def test_marks_all_read(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.mark_all_read.return_value = 5
        client = logged_in_client()
        response = client.post("/api/v1/alerts/mark-all-read")
        assert response.status_code == 200
        assert response.get_json() == {"marked": 5}

    def test_filters_from_body(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.mark_all_read.return_value = 0
        client = logged_in_client()
        client.post(
            "/api/v1/alerts/mark-all-read",
            json={"source": "motion", "severity": "warning"},
        )
        kwargs = app.alert_center.mark_all_read.call_args.kwargs
        assert kwargs["source"] == "motion"
        assert kwargs["severity"] == "warning"

    def test_filters_from_query_string(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.mark_all_read.return_value = 0
        client = logged_in_client()
        client.post(
            "/api/v1/alerts/mark-all-read?source=audit&before=2026-04-30T00:00:00Z"
        )
        kwargs = app.alert_center.mark_all_read.call_args.kwargs
        assert kwargs["source"] == "audit"
        assert kwargs["before"] == "2026-04-30T00:00:00Z"

    def test_body_wins_over_query_string(self, app, logged_in_client):
        app.alert_center = MagicMock()
        app.alert_center.mark_all_read.return_value = 0
        client = logged_in_client()
        client.post(
            "/api/v1/alerts/mark-all-read?source=audit",
            json={"source": "motion"},
        )
        assert app.alert_center.mark_all_read.call_args.kwargs["source"] == "motion"
