# REQ: SWR-024, SWR-032, SWR-045; RISK: RISK-012, RISK-015, RISK-020; SEC: SC-012, SC-020, SC-021; TEST: TC-023, TC-029, TC-042
"""Focused tests for the time-health system routes."""

from unittest.mock import MagicMock


class TestTimeHealthGet:
    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.get("/api/v1/system/time/health").status_code == 403

    def test_returns_service_payload(self, app, logged_in_client):
        app.time_health_service = MagicMock()
        app.time_health_service.compute_health.return_value = {
            "state": "amber",
            "server": {
                "ntp_active": True,
                "ntp_synchronized": False,
                "unsynced_seconds": 42,
                "last_sync_time": "",
            },
            "cameras": [],
            "worst_camera": None,
            "worst_drift_seconds": None,
        }

        client = logged_in_client()
        response = client.get("/api/v1/system/time/health")

        assert response.status_code == 200
        assert response.get_json()["state"] == "amber"


class TestTimeHealthResync:
    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert (
            client.post(
                "/api/v1/system/time/resync", json={"target": "server"}
            ).status_code
            == 403
        )

    def test_requires_json_body(self, logged_in_client):
        client = logged_in_client()
        assert client.post("/api/v1/system/time/resync").status_code == 400

    def test_requires_target(self, logged_in_client):
        client = logged_in_client()
        response = client.post("/api/v1/system/time/resync", json={})
        assert response.status_code == 400
        assert "target" in response.get_json()["error"]

    def test_propagates_service_error(self, app, logged_in_client):
        app.time_health_service = MagicMock()
        app.time_health_service.request_resync.return_value = (
            "Camera not found",
            404,
            False,
        )

        client = logged_in_client()
        response = client.post(
            "/api/v1/system/time/resync", json={"target": "cam-missing"}
        )

        assert response.status_code == 404
        assert response.get_json()["error"] == "Camera not found"

    def test_success_writes_audit_row(self, app, logged_in_client):
        app.time_health_service = MagicMock()
        app.time_health_service.request_resync.return_value = (
            "Time resync queued",
            200,
            True,
        )
        app.audit = MagicMock()

        client = logged_in_client()
        app.audit.log_event.reset_mock()
        response = client.post("/api/v1/system/time/resync", json={"target": "cam-001"})

        assert response.status_code == 200
        assert response.get_json()["message"] == "Time resync queued"
        app.audit.log_event.assert_called_once_with(
            "TIME_RESYNC_REQUESTED",
            user="admin",
            ip="127.0.0.1",
            detail="target=cam-001",
        )

    def test_already_queued_skips_audit(self, app, logged_in_client):
        app.time_health_service = MagicMock()
        app.time_health_service.request_resync.return_value = (
            "already queued",
            200,
            False,
        )
        app.audit = MagicMock()

        client = logged_in_client()
        app.audit.log_event.reset_mock()
        response = client.post("/api/v1/system/time/resync", json={"target": "server"})

        assert response.status_code == 200
        assert response.get_json()["message"] == "already queued"
        app.audit.log_event.assert_not_called()
