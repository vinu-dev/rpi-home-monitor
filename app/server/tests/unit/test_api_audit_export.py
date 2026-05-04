# REQ: SWR-002, SWR-009, SWR-045; RISK: RISK-002, RISK-020, RISK-021; SEC: SC-001, SC-008, SC-021; TEST: TC-011, TC-017, TC-042
"""Focused tests for the audit export endpoint."""

import csv
import io
import json
import time
from unittest.mock import MagicMock

import monitor.api.audit as audit_api


def _detail_payload(mock_audit, event_name):
    for call in mock_audit.log_event.call_args_list:
        if call.args and call.args[0] == event_name:
            return json.loads(call.kwargs["detail"])
    raise AssertionError(f"{event_name} was not logged")


class TestAuditExportEndpoint:
    def setup_method(self):
        audit_api._export_attempts_by_ip.clear()
        audit_api._export_attempts_by_user.clear()

    def teardown_method(self):
        audit_api._export_attempts_by_ip.clear()
        audit_api._export_attempts_by_user.clear()

    def test_requires_auth(self, client):
        response = client.get("/api/v1/audit/events/export?format=csv")
        assert response.status_code == 401

    def test_viewer_forbidden(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.get("/api/v1/audit/events/export?format=csv")
        assert response.status_code == 403

    def test_missing_csrf_returns_403(self, logged_in_client):
        client = logged_in_client()
        client.environ_base.pop("HTTP_X_CSRF_TOKEN", None)

        response = client.get("/api/v1/audit/events/export?format=csv")

        assert response.status_code == 403
        assert response.get_json() == {"error": "Invalid CSRF token"}

    def test_invalid_format_returns_400(self, logged_in_client):
        client = logged_in_client()

        response = client.get("/api/v1/audit/events/export?format=xml")

        assert response.status_code == 400
        assert response.get_json() == {"error": "format must be csv or json"}

    def test_invalid_start_returns_400(self, logged_in_client):
        client = logged_in_client()

        response = client.get("/api/v1/audit/events/export?format=csv&start=not-a-date")

        assert response.status_code == 400
        assert "start must be ISO-8601 UTC" in response.get_json()["error"]

    def test_start_after_end_returns_400(self, logged_in_client):
        client = logged_in_client()

        response = client.get(
            "/api/v1/audit/events/export?format=csv"
            "&start=2026-05-04T12:00:00Z"
            "&end=2026-05-04T10:00:00Z"
        )

        assert response.status_code == 400
        assert response.get_json() == {"error": "start must be <= end"}

    def test_csv_export_streams_attachment_and_audits_result(
        self, app, logged_in_client
    ):
        app.audit = MagicMock()
        app.audit.iter_events.return_value = iter(
            [
                {
                    "timestamp": "2026-05-04T09:00:00Z",
                    "event": "LOGIN_SUCCESS",
                    "user": "admin",
                    "ip": "192.168.1.10",
                    "detail": "signed in",
                },
                {
                    "timestamp": "2026-05-04T09:05:00Z",
                    "event": "LOGIN_FAILED",
                    "user": "viewer",
                    "ip": "192.168.1.11",
                    "detail": "=cmd|calc",
                },
            ]
        )
        client = logged_in_client()

        response = client.get(
            "/api/v1/audit/events/export?format=csv"
            "&event_type=LOGIN_SUCCESS,LOGIN_FAILED"
            "&actor=admin"
        )

        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/csv; charset=utf-8")
        assert "attachment;" in response.headers["Content-Disposition"]
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
        assert rows[0] == ["timestamp", "event", "user", "ip", "detail"]
        assert rows[1] == [
            "2026-05-04T09:00:00Z",
            "LOGIN_SUCCESS",
            "admin",
            "192.168.1.10",
            "signed in",
        ]
        assert rows[2][4] == "'=cmd|calc"
        app.audit.iter_events.assert_called_once_with(
            start="",
            end="",
            event_type="LOGIN_SUCCESS,LOGIN_FAILED",
            actor="admin",
        )
        detail = _detail_payload(app.audit, "AUDIT_LOG_EXPORTED")
        assert detail["row_count"] == 2
        assert detail["truncated"] is False
        assert detail["filters"]["event_type"] == "LOGIN_SUCCESS,LOGIN_FAILED"
        assert detail["filters"]["actor"] == "admin"

    def test_csv_export_quotes_rfc4180_fields(self, app, logged_in_client):
        app.audit = MagicMock()
        detail = 'operator said "door, open"\nnext line'
        app.audit.iter_events.return_value = iter(
            [
                {
                    "timestamp": "2026-05-04T09:00:00Z",
                    "event": "LOGIN_SUCCESS",
                    "user": "admin",
                    "ip": "192.168.1.10",
                    "detail": detail,
                }
            ]
        )
        client = logged_in_client()

        response = client.get("/api/v1/audit/events/export?format=csv")

        body = response.get_data(as_text=True)
        assert '"operator said ""door, open""\nnext line"' in body
        rows = list(csv.reader(io.StringIO(body)))
        assert rows[1][4] == detail

    def test_json_export_returns_array(self, app, logged_in_client):
        app.audit = MagicMock()
        app.audit.iter_events.return_value = iter(
            [
                {
                    "timestamp": "2026-05-04T09:00:00Z",
                    "event": "LOGIN_SUCCESS",
                    "user": "admin",
                    "ip": "",
                    "detail": "",
                }
            ]
        )
        client = logged_in_client()

        response = client.get("/api/v1/audit/events/export?format=json")

        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("application/json")
        assert json.loads(response.get_data(as_text=True)) == [
            {
                "timestamp": "2026-05-04T09:00:00Z",
                "event": "LOGIN_SUCCESS",
                "user": "admin",
                "ip": "",
                "detail": "",
            }
        ]

    def test_rate_limited_export_returns_429_and_audits_denial(
        self, app, logged_in_client
    ):
        now = time.time()
        audit_api._export_attempts_by_user["admin"] = [
            now
        ] * audit_api.EXPORT_RATE_LIMIT_BLOCK
        audit_api._export_attempts_by_ip["127.0.0.1"] = [
            now
        ] * audit_api.EXPORT_RATE_LIMIT_BLOCK
        app.audit = MagicMock()
        client = logged_in_client()

        response = client.get("/api/v1/audit/events/export?format=csv")

        assert response.status_code == 429
        assert response.headers["Retry-After"]
        assert response.get_json() == {"error": "Export rate-limited. Try again later."}
        detail = _detail_payload(app.audit, "AUDIT_LOG_EXPORT_DENIED")
        assert detail["reason"] == "rate_limited"
        assert detail["format"] == "csv"
