# REQ: SWR-033, SWR-041; RISK: RISK-016; SEC: SC-015; TEST: TC-031
"""Integration tests for notification quiet hours."""

from monitor.models import Camera, MotionEvent


def _set_user_quiet_hours(app, username, *, schedule, camera_overrides=None):
    user = app.store.get_user_by_username(username)
    user.notification_prefs = {
        "enabled": True,
        "cameras": camera_overrides or {},
    }
    user.notification_schedule = schedule
    app.store.save_user(user)


def _append_motion(app, *, event_id, camera_id, started_at, ended_at):
    app.motion_event_store.append(
        MotionEvent(
            id=event_id,
            camera_id=camera_id,
            started_at=started_at,
            ended_at=ended_at,
            peak_score=0.18,
            duration_seconds=10.0,
        )
    )


class TestQuietHours:
    def test_pending_suppressed_but_alert_center_keeps_event(
        self, app, logged_in_client
    ):
        client = logged_in_client()
        app.store.save_camera(Camera(id="cam-d8ee", name="Front Door", status="online"))
        _set_user_quiet_hours(
            app,
            "admin",
            schedule=[{"days": ["mon"], "start": "22:00", "end": "06:00"}],
        )
        _append_motion(
            app,
            event_id="m-quiet",
            camera_id="cam-d8ee",
            started_at="2026-06-01T21:20:00Z",
            ended_at="2026-06-01T21:30:00Z",
        )

        pending = client.get("/api/v1/notifications/pending").get_json()
        alerts = client.get("/api/v1/alerts/").get_json()
        audit_rows = app.audit.get_events(limit=10, event_type="NOTIFICATION_QUIETED")

        assert pending["count"] == 0
        assert any(alert["id"] == "motion:m-quiet" for alert in alerts["alerts"])
        assert len(audit_rows) == 1
        assert "camera_id=cam-d8ee" in audit_rows[0]["detail"]

    def test_empty_camera_override_bypasses_user_schedule(self, app, logged_in_client):
        client = logged_in_client()
        app.store.save_camera(Camera(id="cam-d8ee", name="Front Door", status="online"))
        _set_user_quiet_hours(
            app,
            "admin",
            schedule=[{"days": ["mon"], "start": "22:00", "end": "06:00"}],
            camera_overrides={"cam-d8ee": {"quiet_schedule": []}},
        )
        _append_motion(
            app,
            event_id="m-loud",
            camera_id="cam-d8ee",
            started_at="2026-06-01T21:20:00Z",
            ended_at="2026-06-01T21:30:00Z",
        )

        pending = client.get("/api/v1/notifications/pending").get_json()

        assert pending["count"] == 1
        assert pending["alerts"][0]["alert_id"] == "motion:m-loud"

    def test_quiet_audit_rate_limited_for_two_events_in_same_window(
        self, app, logged_in_client
    ):
        client = logged_in_client()
        app.store.save_camera(Camera(id="cam-d8ee", name="Front Door", status="online"))
        _set_user_quiet_hours(
            app,
            "admin",
            schedule=[{"days": ["mon"], "start": "22:00", "end": "06:00"}],
        )
        _append_motion(
            app,
            event_id="m-quiet-1",
            camera_id="cam-d8ee",
            started_at="2026-06-01T21:20:00Z",
            ended_at="2026-06-01T21:30:00Z",
        )
        _append_motion(
            app,
            event_id="m-quiet-2",
            camera_id="cam-d8ee",
            started_at="2026-06-01T21:31:00Z",
            ended_at="2026-06-01T21:32:00Z",
        )

        pending = client.get("/api/v1/notifications/pending").get_json()
        audit_rows = app.audit.get_events(limit=10, event_type="NOTIFICATION_QUIETED")

        assert pending["count"] == 0
        assert len(audit_rows) == 1
