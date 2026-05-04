# REQ: SWR-002, SWR-009; RISK: RISK-002, RISK-020; SEC: SC-001, SC-008; TEST: TC-011, TC-017
"""Integration coverage for streamed audit exports."""

import json


class TestAuditExportStreaming:
    def test_csv_export_is_streamed_and_disconnect_is_audited(
        self, app, logged_in_client
    ):
        client = logged_in_client()
        app.audit.clear_events(user="admin")
        app.audit.log_event("LOGIN_SUCCESS", user="admin", ip="192.168.1.10")
        app.audit.log_event("LOGIN_FAILED", user="viewer", ip="192.168.1.11")

        response = client.get(
            "/api/v1/audit/events/export?format=csv"
            "&event_type=LOGIN_SUCCESS,LOGIN_FAILED",
            buffered=False,
        )

        assert response.status_code == 200
        assert response.is_streamed
        assert (
            next(response.response).decode("utf-8")
            == "timestamp,event,user,ip,detail\r\n"
        )
        assert "LOGIN_SUCCESS" in next(response.response).decode("utf-8")
        response.close()

        exports = app.audit.get_events(limit=5, event_type="AUDIT_LOG_EXPORTED")
        detail = json.loads(exports[0]["detail"])
        assert detail["truncated"] is True
        assert detail["reason"] == "client_disconnect"
        assert detail["row_count"] == 1

    def test_export_snapshot_excludes_new_events_after_stream_starts(
        self, app, logged_in_client
    ):
        client = logged_in_client()
        app.audit.clear_events(user="admin")
        app.audit.log_event("LOGIN_SUCCESS", user="admin", detail="before export")

        response = client.get(
            "/api/v1/audit/events/export?format=json&event_type=LOGIN_SUCCESS",
            buffered=False,
        )

        assert next(response.response).decode("utf-8") == "["
        app.audit.log_event("LOGIN_FAILED", user="viewer", detail="after export start")
        payload = "".join(chunk.decode("utf-8") for chunk in response.response)
        response.close()

        exported = json.loads("[" + payload)
        assert [entry["detail"] for entry in exported] == ["before export"]
