# REQ: SWR-024, SWR-032; RISK: RISK-012, RISK-015; SEC: SC-012, SC-020; TEST: TC-023, TC-029
"""Integration coverage for time-health summary behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from re import match

from monitor.models import Camera


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestTimeHealthSummary:
    def test_camera_drift_flips_summary_amber(self, app, logged_in_client):
        now = datetime.now(UTC)
        app.store.save_camera(
            Camera(
                id="cam-lr",
                name="Living Room",
                status="online",
                last_seen=_iso(now),
                last_beat_camera_ts=_iso(now - timedelta(seconds=4)),
            )
        )
        app.settings_service.get_time_status = lambda: {
            "ntp_active": True,
            "ntp_synchronized": True,
            "system_time": "Sat 2026-05-04 12:00:00 UTC",
            "rtc_time": "Sat 2026-05-04 12:00:00 UTC",
        }
        app.settings_service.get_timesync_status = lambda: {"last_sync_time": ""}

        client = logged_in_client()
        response = client.get("/api/v1/system/summary")

        assert response.status_code == 200
        data = response.get_json()
        assert data["state"] == "amber"
        assert data["deep_link"] == "/settings#time-health"
        assert match(
            r"^Camera \*Living Room\* clock drifted \+4s — resync$",
            data["summary"],
        )
        assert data["details"]["time_health"]["state"] == "amber"
