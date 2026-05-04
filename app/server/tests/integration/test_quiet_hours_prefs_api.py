# REQ: SWR-033, SWR-041; RISK: RISK-016; SEC: SC-015; TEST: TC-031
"""Integration tests for quiet-hours notification prefs API."""


class TestQuietHoursPrefsApi:
    def test_put_and_get_round_trip_schedule_and_camera_override(
        self, logged_in_client
    ):
        client = logged_in_client()

        response = client.put(
            "/api/v1/notifications/prefs",
            json={
                "enabled": True,
                "notification_schedule": [
                    {"days": ["mon", "tue"], "start": "22:00", "end": "06:00"}
                ],
                "cameras": {
                    "cam-d8ee": {
                        "quiet_schedule": [
                            {"days": ["fri"], "start": "18:00", "end": "23:00"}
                        ]
                    }
                },
            },
        )
        assert response.status_code == 200
        data = response.get_json()["prefs"]
        assert data["notification_schedule"][0]["start"] == "22:00"
        assert data["cameras"]["cam-d8ee"]["quiet_schedule"][0]["end"] == "23:00"

        fetched = client.get("/api/v1/notifications/prefs")
        assert fetched.status_code == 200
        prefs = fetched.get_json()["prefs"]
        assert prefs["notification_schedule"][0]["days"] == ["mon", "tue"]
        assert prefs["cameras"]["cam-d8ee"]["quiet_schedule"][0]["start"] == "18:00"

    def test_null_camera_quiet_schedule_restores_inherit(self, logged_in_client):
        client = logged_in_client()

        response = client.put(
            "/api/v1/notifications/prefs",
            json={
                "cameras": {
                    "cam-d8ee": {
                        "enabled": False,
                        "quiet_schedule": [
                            {"days": ["fri"], "start": "18:00", "end": "23:00"}
                        ],
                    }
                }
            },
        )
        assert response.status_code == 200

        cleared = client.put(
            "/api/v1/notifications/prefs",
            json={
                "cameras": {
                    "cam-d8ee": {
                        "enabled": False,
                        "quiet_schedule": None,
                    }
                }
            },
        )
        assert cleared.status_code == 200
        prefs = cleared.get_json()["prefs"]
        assert prefs["cameras"]["cam-d8ee"]["enabled"] is False
        assert "quiet_schedule" not in prefs["cameras"]["cam-d8ee"]
