# REQ: SWR-033, SWR-041; RISK: RISK-016; SEC: SC-015; TEST: TC-031
"""Unit tests for quiet-hours schedule evaluation."""

from datetime import UTC, datetime

from monitor.services.notification_schedule import (
    evaluate_quiet_hours,
    validate_schedule,
)


class TestValidateSchedule:
    def test_rejects_empty_days(self):
        err = validate_schedule([{"days": [], "start": "22:00", "end": "06:00"}])
        assert "days must be a non-empty list" in err

    def test_rejects_zero_length_window(self):
        err = validate_schedule([{"days": ["mon"], "start": "22:00", "end": "22:00"}])
        assert "must not use the same start and end time" in err

    def test_rejects_bad_time_format(self):
        err = validate_schedule([{"days": ["mon"], "start": "9pm", "end": "06:00"}])
        assert ".start must match HH:MM" in err


class TestEvaluateQuietHours:
    def test_overnight_user_window_matches_local_time(self):
        decision = evaluate_quiet_hours(
            now=datetime(2026, 6, 1, 21, 30, tzinfo=UTC),
            user_schedule=[{"days": ["mon"], "start": "22:00", "end": "06:00"}],
            camera_override=None,
            tz="Europe/Dublin",
        )
        assert decision.quiet is True
        assert decision.source == "user"

    def test_camera_empty_override_means_always_loud(self):
        decision = evaluate_quiet_hours(
            now=datetime(2026, 6, 1, 21, 30, tzinfo=UTC),
            user_schedule=[{"days": ["mon"], "start": "22:00", "end": "06:00"}],
            camera_override=[],
            tz="Europe/Dublin",
        )
        assert decision.quiet is False
        assert decision.source == "camera"

    def test_camera_override_replaces_user_schedule(self):
        decision = evaluate_quiet_hours(
            now=datetime(2026, 6, 1, 12, 30, tzinfo=UTC),
            user_schedule=[{"days": ["mon"], "start": "22:00", "end": "06:00"}],
            camera_override=[{"days": ["mon"], "start": "13:00", "end": "14:00"}],
            tz="Europe/Dublin",
        )
        assert decision.quiet is True
        assert decision.source == "camera"

    def test_malformed_entry_is_skipped(self):
        decision = evaluate_quiet_hours(
            now=datetime(2026, 6, 1, 21, 30, tzinfo=UTC),
            user_schedule=[
                {"days": ["mon"], "start": "bad", "end": "06:00"},
                {"days": ["mon"], "start": "22:00", "end": "06:00"},
            ],
            camera_override=None,
            tz="Europe/Dublin",
        )
        assert decision.quiet is True

    def test_spring_forward_uses_timezone_converted_clock(self):
        decision = evaluate_quiet_hours(
            now=datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
            user_schedule=[{"days": ["sun"], "start": "01:00", "end": "02:00"}],
            camera_override=None,
            tz="Europe/Dublin",
        )
        assert decision.quiet is False

    def test_fall_back_repeated_hour_still_matches(self):
        schedule = [{"days": ["sun"], "start": "01:00", "end": "02:00"}]

        first = evaluate_quiet_hours(
            now=datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
            user_schedule=schedule,
            camera_override=None,
            tz="Europe/Dublin",
        )
        second = evaluate_quiet_hours(
            now=datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
            user_schedule=schedule,
            camera_override=None,
            tz="Europe/Dublin",
        )

        assert first.quiet is True
        assert second.quiet is True
