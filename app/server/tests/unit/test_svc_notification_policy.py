# REQ: SWR-033, SWR-041; RISK: RISK-016; SEC: SC-015; TEST: TC-031
"""Tests for NotificationPolicyService — ADR-0027 #128 Backend/API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

from monitor.services.notification_policy_service import NotificationPolicyService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeMotionEvent:
    id: str
    camera_id: str
    started_at: str
    ended_at: str | None = None
    peak_score: float = 0.18
    duration_seconds: float = 5.0
    clip_ref: dict | None = None


def _now_z(offset_seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _make_camera(**overrides):
    defaults = {
        "id": "cam-d8ee",
        "name": "Front Door",
        "notification_rule": {
            "enabled": True,
            "min_duration_seconds": 3,
            "coalesce_seconds": 60,
        },
        "last_notification_at": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_user(username="alice", **overrides):
    defaults = {
        "id": f"user-{username}",
        "username": username,
        "notification_prefs": {"enabled": True, "cameras": {}},
        "notification_schedule": [],
        "last_notification_seen_at": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(*, cameras=None, users=None, motion_events=None, audit_events=None):
    store = MagicMock()
    store.get_cameras.return_value = cameras or []
    store.get_users.return_value = users or []
    store.get_settings.return_value = SimpleNamespace(timezone="Europe/Dublin")
    store.save_camera = MagicMock()
    store.save_user = MagicMock()

    motion = MagicMock()
    motion.list_events.return_value = motion_events or []
    motion.get.side_effect = lambda eid: next(
        (e for e in (motion_events or []) if e.id == eid), None
    )
    audit = MagicMock()
    audit.get_events.return_value = audit_events or []

    return (
        NotificationPolicyService(
            store=store,
            motion_event_store=motion,
            audit_logger=audit,
        ),
        store,
        motion,
    )


# ---------------------------------------------------------------------------
# select_for_user — the decision tree
# ---------------------------------------------------------------------------


class TestUserGate:
    def test_returns_empty_when_user_has_notifications_disabled(self):
        cam = _make_camera()
        usr = _make_user(notification_prefs={"enabled": False, "cameras": {}})
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
            duration_seconds=10,
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert svc.select_for_user(user="alice") == []

    def test_returns_empty_for_unknown_user(self):
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
        )
        svc, _, _ = _make_service(motion_events=[evt])
        assert svc.select_for_user(user="nobody") == []


class TestPerCameraEnable:
    def test_camera_disabled_drops_event(self):
        cam = _make_camera(
            notification_rule={
                "enabled": False,
                "min_duration_seconds": 3,
                "coalesce_seconds": 60,
            }
        )
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert svc.select_for_user(user="alice") == []

    def test_per_user_override_can_disable_camera(self):
        cam = _make_camera()  # camera default: enabled
        usr = _make_user(
            notification_prefs={
                "enabled": True,
                "cameras": {"cam-d8ee": {"enabled": False}},
            }
        )
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert svc.select_for_user(user="alice") == []

    def test_per_user_override_can_enable_camera(self):
        # Camera default disabled, user-level override enables.
        cam = _make_camera(
            notification_rule={
                "enabled": False,
                "min_duration_seconds": 3,
                "coalesce_seconds": 60,
            }
        )
        usr = _make_user(
            notification_prefs={
                "enabled": True,
                "cameras": {"cam-d8ee": {"enabled": True}},
            }
        )
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert len(svc.select_for_user(user="alice")) == 1


class TestDurationFilter:
    def test_below_min_duration_dropped(self):
        cam = _make_camera()  # default min 3s
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-2),
            ended_at=_now_z(),
            duration_seconds=2,  # below default 3s
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert svc.select_for_user(user="alice") == []

    def test_at_min_duration_passes(self):
        cam = _make_camera()
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-3),
            ended_at=_now_z(),
            duration_seconds=3,
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert len(svc.select_for_user(user="alice")) == 1

    def test_per_user_min_duration_override(self):
        cam = _make_camera()  # default 3s
        usr = _make_user(
            notification_prefs={
                "enabled": True,
                "cameras": {"cam-d8ee": {"min_duration_seconds": 10}},
            }
        )
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
            duration_seconds=5,  # passes camera's 3s but not user's 10s
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert svc.select_for_user(user="alice") == []


class TestPeakScoreFilter:
    def test_below_motion_threshold_dropped(self):
        # peak_score below MOTION_NOTIFICATION_THRESHOLD (0.05)
        cam = _make_camera()
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
            duration_seconds=10,
            peak_score=0.01,
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert svc.select_for_user(user="alice") == []


class TestCoalesceWindow:
    def test_within_cooldown_suppresses(self):
        # Camera notified 30s ago; default coalesce is 60s.
        cam = _make_camera(last_notification_at=_now_z(-30))
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
            duration_seconds=10,
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert svc.select_for_user(user="alice") == []

    def test_after_cooldown_emits(self):
        cam = _make_camera(last_notification_at=_now_z(-300))  # 5 min ago
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
            duration_seconds=10,
        )
        svc, store, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        result = svc.select_for_user(user="alice")
        assert len(result) == 1
        # And the camera's last_notification_at got stamped.
        assert cam.last_notification_at  # non-empty
        store.save_camera.assert_called_once()

    def test_corrupt_last_notification_at_fails_open(self):
        cam = _make_camera(last_notification_at="not-a-timestamp")
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
            duration_seconds=10,
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        # Corrupt timestamp: emit anyway. Same fail-open as #136.
        assert len(svc.select_for_user(user="alice")) == 1


class TestQuietHours:
    def test_quiet_hours_suppresses_and_does_not_stamp_camera(self):
        cam = _make_camera(last_notification_at="")
        usr = _make_user(
            notification_schedule=[
                {"days": ["mon"], "start": "22:00", "end": "06:00"},
            ]
        )
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at="2026-06-01T21:20:00Z",
            ended_at="2026-06-01T21:30:00Z",
            duration_seconds=10,
        )
        store = MagicMock()
        store.get_cameras.return_value = [cam]
        store.get_users.return_value = [usr]
        store.get_settings.return_value = SimpleNamespace(timezone="Europe/Dublin")
        store.save_camera = MagicMock()
        store.save_user = MagicMock()
        motion = MagicMock()
        motion.list_events.return_value = [evt]
        motion.get.return_value = evt
        audit = MagicMock()

        svc = NotificationPolicyService(
            store=store,
            motion_event_store=motion,
            audit=audit,
        )

        assert svc.select_for_user(user="alice") == []
        assert cam.last_notification_at == ""
        store.save_camera.assert_not_called()
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "NOTIFICATION_QUIETED"
        assert "camera_id=cam-d8ee" in audit.log_event.call_args.kwargs["detail"]

    def test_empty_camera_quiet_override_bypasses_user_schedule(self):
        cam = _make_camera(last_notification_at="")
        usr = _make_user(
            notification_schedule=[
                {"days": ["mon"], "start": "22:00", "end": "06:00"},
            ],
            notification_prefs={
                "enabled": True,
                "cameras": {"cam-d8ee": {"quiet_schedule": []}},
            },
        )
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at="2026-06-01T21:20:00Z",
            ended_at="2026-06-01T21:30:00Z",
            duration_seconds=10,
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])

        assert len(svc.select_for_user(user="alice")) == 1

    def test_quiet_audit_is_rate_limited_per_window(self):
        cam = _make_camera(last_notification_at="")
        usr = _make_user(
            notification_schedule=[
                {"days": ["mon"], "start": "22:00", "end": "06:00"},
            ]
        )
        events = [
            _FakeMotionEvent(
                id="m1",
                camera_id="cam-d8ee",
                started_at="2026-06-01T21:20:00Z",
                ended_at="2026-06-01T21:30:00Z",
                duration_seconds=10,
            ),
            _FakeMotionEvent(
                id="m2",
                camera_id="cam-d8ee",
                started_at="2026-06-01T21:31:00Z",
                ended_at="2026-06-01T21:32:00Z",
                duration_seconds=10,
            ),
        ]
        store = MagicMock()
        store.get_cameras.return_value = [cam]
        store.get_users.return_value = [usr]
        store.get_settings.return_value = SimpleNamespace(timezone="Europe/Dublin")
        store.save_camera = MagicMock()
        store.save_user = MagicMock()
        motion = MagicMock()
        motion.list_events.return_value = events
        motion.get.side_effect = lambda eid: next(e for e in events if e.id == eid)
        audit = MagicMock()

        svc = NotificationPolicyService(
            store=store,
            motion_event_store=motion,
            audit=audit,
        )

        assert svc.select_for_user(user="alice") == []
        assert audit.log_event.call_count == 1


class TestSinceFilter:
    def test_since_anchor_filters_older_events(self):
        cam = _make_camera()
        usr = _make_user()
        evts = [
            _FakeMotionEvent(
                id="old",
                camera_id="cam-d8ee",
                started_at=_now_z(-300),
                ended_at=_now_z(-295),
                duration_seconds=5,
            ),
            _FakeMotionEvent(
                id="new",
                camera_id="cam-d8ee",
                started_at=_now_z(-30),
                ended_at=_now_z(-25),
                duration_seconds=5,
            ),
        ]
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=evts)
        result = svc.select_for_user(user="alice", since=_now_z(-100))
        # Only the "new" event passes.
        assert len(result) == 1
        assert result[0]["alert_id"] == "motion:new"

    def test_default_since_is_user_seen_pointer(self):
        cam = _make_camera()
        usr = _make_user(last_notification_seen_at=_now_z(-100))
        evts = [
            _FakeMotionEvent(
                id="old",
                camera_id="cam-d8ee",
                started_at=_now_z(-200),
                ended_at=_now_z(-195),
                duration_seconds=5,
            ),
            _FakeMotionEvent(
                id="new",
                camera_id="cam-d8ee",
                started_at=_now_z(-30),
                ended_at=_now_z(-25),
                duration_seconds=5,
            ),
        ]
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=evts)
        # No explicit since → uses user's seen pointer.
        result = svc.select_for_user(user="alice")
        assert len(result) == 1
        assert result[0]["alert_id"] == "motion:new"


class TestInProgressEvents:
    def test_unended_event_not_eligible(self):
        cam = _make_camera()
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="running",
            camera_id="cam-d8ee",
            started_at=_now_z(-3),
            ended_at=None,  # in progress
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        assert svc.select_for_user(user="alice") == []


class TestThrottleAuditNotifications:
    def test_emits_throttle_audit_notification(self):
        cam = _make_camera(id="cam-d8ee", name="Front Door")
        usr = _make_user()
        audit_events = [
            {
                "timestamp": "2026-05-04T12:00:00Z",
                "event": "CAMERA_THROTTLED",
                "user": "camera",
                "ip": "",
                "detail": (
                    "camera cam-d8ee sticky throttle bits set: "
                    "Under-voltage, Frequency capped"
                ),
            }
        ]
        svc, _, _ = _make_service(cameras=[cam], users=[usr], audit_events=audit_events)
        result = svc.select_for_user(user="alice")
        assert len(result) == 1
        assert result[0]["alert_id"].startswith("throttle:")
        assert result[0]["camera_name"] == "Front Door"
        assert result[0]["deep_link"] == "/dashboard#camera-cam-d8ee"
        assert result[0]["title"] == "Camera health warning: Front Door"
        assert "Under-voltage" in result[0]["body"]

    def test_camera_disable_suppresses_throttle_audit_notification(self):
        cam = _make_camera(
            notification_rule={
                "enabled": False,
                "min_duration_seconds": 3,
                "coalesce_seconds": 60,
            }
        )
        usr = _make_user()
        audit_events = [
            {
                "timestamp": "2026-05-04T12:00:00Z",
                "event": "CAMERA_THROTTLED",
                "user": "camera",
                "ip": "",
                "detail": "camera cam-d8ee sticky throttle bits set: Under-voltage",
            }
        ]
        svc, _, _ = _make_service(cameras=[cam], users=[usr], audit_events=audit_events)
        assert svc.select_for_user(user="alice") == []

    def test_quiet_hours_suppresses_throttle_audit_notification(self):
        cam = _make_camera(id="cam-d8ee", name="Front Door")
        usr = _make_user(
            notification_schedule=[
                {"days": ["mon"], "start": "22:00", "end": "06:00"},
            ]
        )
        audit_events = [
            {
                "timestamp": "2026-06-01T21:30:00Z",
                "event": "CAMERA_THROTTLED",
                "user": "camera",
                "ip": "",
                "detail": "camera cam-d8ee sticky throttle bits set: Under-voltage",
            }
        ]
        svc, _, _ = _make_service(cameras=[cam], users=[usr], audit_events=audit_events)

        assert svc.select_for_user(user="alice") == []
        svc._audit.log_event.assert_called_once()
        assert svc._audit.log_event.call_args[0][0] == "NOTIFICATION_QUIETED"
        detail = svc._audit.log_event.call_args.kwargs["detail"]
        assert "camera_id=cam-d8ee" in detail
        assert "class=throttle" in detail


class TestWireFormat:
    def test_wire_includes_camera_name_and_deep_link(self):
        cam = _make_camera(name="Front Door")
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
            duration_seconds=10,
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        result = svc.select_for_user(user="alice")
        assert len(result) == 1
        n = result[0]
        assert n["camera_name"] == "Front Door"
        assert n["deep_link"] == "/events/m1"
        assert n["snapshot_url"] is None  # no clip_ref → text-only

    def test_wire_emits_snapshot_url_when_clip_ref_present(self):
        cam = _make_camera()
        usr = _make_user()
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-5),
            ended_at=_now_z(),
            duration_seconds=10,
            clip_ref={
                "camera_id": "cam-d8ee",
                "date": "2026-05-02",
                "filename": "20260502_080000.mp4",
            },
        )
        svc, _, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        result = svc.select_for_user(user="alice")
        assert (
            result[0]["snapshot_url"]
            == "/api/v1/recordings/cam-d8ee/2026-05-02/20260502_080000.jpg"
        )


# ---------------------------------------------------------------------------
# mark_seen
# ---------------------------------------------------------------------------


class TestMarkSeen:
    def test_advances_last_seen_pointer(self):
        cam = _make_camera()
        usr = _make_user(last_notification_seen_at=_now_z(-100))
        evts = [
            _FakeMotionEvent(
                id="m1",
                camera_id="cam-d8ee",
                started_at=_now_z(-30),
                ended_at=_now_z(-25),
            ),
            _FakeMotionEvent(
                id="m2",
                camera_id="cam-d8ee",
                started_at=_now_z(-10),
                ended_at=_now_z(-5),
            ),
        ]
        svc, store, _ = _make_service(cameras=[cam], users=[usr], motion_events=evts)
        marked = svc.mark_seen(user="alice", alert_ids=["motion:m1", "motion:m2"])
        assert marked == 2
        # Should have advanced to the latest started_at.
        assert usr.last_notification_seen_at == evts[1].started_at
        store.save_user.assert_called_once()

    def test_unknown_alert_ids_dont_advance_pointer(self):
        cam = _make_camera()
        original_seen = _now_z(-100)
        usr = _make_user(last_notification_seen_at=original_seen)
        svc, store, _ = _make_service(cameras=[cam], users=[usr], motion_events=[])
        marked = svc.mark_seen(user="alice", alert_ids=["motion:nonexistent"])
        assert marked == 0
        assert usr.last_notification_seen_at == original_seen
        store.save_user.assert_not_called()

    def test_idempotent_when_already_seen(self):
        cam = _make_camera()
        seen = _now_z(-10)
        usr = _make_user(last_notification_seen_at=seen)
        evt = _FakeMotionEvent(
            id="m1",
            camera_id="cam-d8ee",
            started_at=_now_z(-30),  # OLDER than seen pointer
            ended_at=_now_z(-25),
        )
        svc, store, _ = _make_service(cameras=[cam], users=[usr], motion_events=[evt])
        marked = svc.mark_seen(user="alice", alert_ids=["motion:m1"])
        assert marked == 1  # we did process it
        assert usr.last_notification_seen_at == seen  # but pointer didn't go back
        store.save_user.assert_not_called()

    def test_throttle_alert_advances_last_seen_pointer(self):
        cam = _make_camera(id="cam-d8ee")
        usr = _make_user(last_notification_seen_at=_now_z(-300))
        event_ts = _now_z(-100)
        audit_event = {
            "timestamp": event_ts,
            "event": "CAMERA_THROTTLED",
            "user": "camera",
            "ip": "",
            "detail": "camera cam-d8ee sticky throttle bits set: Under-voltage",
        }
        svc, store, _ = _make_service(
            cameras=[cam],
            users=[usr],
            audit_events=[audit_event],
        )
        alert_id = svc.select_for_user(user="alice")[0]["alert_id"]
        marked = svc.mark_seen(user="alice", alert_ids=[alert_id])
        assert marked == 1
        assert usr.last_notification_seen_at == event_ts
        store.save_user.assert_called_once()


# ---------------------------------------------------------------------------
# update_prefs
# ---------------------------------------------------------------------------


class TestUpdatePrefs:
    def test_enable_globally(self):
        usr = _make_user(notification_prefs={"enabled": False, "cameras": {}})
        svc, store, _ = _make_service(users=[usr])
        prefs, err = svc.update_prefs(user="alice", payload={"enabled": True})
        assert err == ""
        assert prefs["enabled"] is True
        assert usr.notification_prefs["enabled"] is True
        store.save_user.assert_called_once()

    def test_rejects_non_bool_enabled(self):
        usr = _make_user()
        svc, _, _ = _make_service(users=[usr])
        _, err = svc.update_prefs(user="alice", payload={"enabled": "yes"})
        assert "boolean" in err

    def test_rejects_out_of_range_min_duration(self):
        usr = _make_user()
        svc, _, _ = _make_service(users=[usr])
        _, err = svc.update_prefs(
            user="alice",
            payload={"cameras": {"cam-d8ee": {"min_duration_seconds": 300}}},
        )
        assert "min_duration" in err

    def test_rejects_out_of_range_coalesce(self):
        usr = _make_user()
        svc, _, _ = _make_service(users=[usr])
        _, err = svc.update_prefs(
            user="alice",
            payload={"cameras": {"cam-d8ee": {"coalesce_seconds": 5}}},
        )
        assert "coalesce" in err

    def test_accepts_notification_schedule(self):
        usr = _make_user(notification_schedule=[])
        svc, store, _ = _make_service(users=[usr])
        prefs, err = svc.update_prefs(
            user="alice",
            payload={
                "notification_schedule": [
                    {"days": ["mon", "tue"], "start": "22:00", "end": "06:00"}
                ]
            },
        )
        assert err == ""
        assert prefs["notification_schedule"][0]["start"] == "22:00"
        assert usr.notification_schedule[0]["end"] == "06:00"
        store.save_user.assert_called_once()

    def test_rejects_zero_length_notification_schedule(self):
        usr = _make_user(notification_schedule=[])
        svc, _, _ = _make_service(users=[usr])
        _, err = svc.update_prefs(
            user="alice",
            payload={
                "notification_schedule": [
                    {"days": ["mon"], "start": "22:00", "end": "22:00"}
                ]
            },
        )
        assert "same start and end time" in err

    def test_camera_quiet_schedule_null_clears_but_preserves_other_overrides(self):
        usr = _make_user(
            notification_prefs={
                "enabled": True,
                "cameras": {
                    "cam-d8ee": {
                        "enabled": False,
                        "quiet_schedule": [
                            {"days": ["mon"], "start": "22:00", "end": "06:00"}
                        ],
                    }
                },
            }
        )
        svc, _, _ = _make_service(users=[usr])
        prefs, err = svc.update_prefs(
            user="alice",
            payload={
                "cameras": {"cam-d8ee": {"enabled": False, "quiet_schedule": None}}
            },
        )
        assert err == ""
        assert prefs["cameras"]["cam-d8ee"]["enabled"] is False
        assert "quiet_schedule" not in prefs["cameras"]["cam-d8ee"]

    def test_null_camera_override_clears(self):
        usr = _make_user(
            notification_prefs={
                "enabled": True,
                "cameras": {"cam-d8ee": {"enabled": False}},
            }
        )
        svc, _, _ = _make_service(users=[usr])
        prefs, err = svc.update_prefs(
            user="alice", payload={"cameras": {"cam-d8ee": None}}
        )
        assert err == ""
        assert "cam-d8ee" not in prefs["cameras"]

    def test_partial_update_preserves_unmentioned_keys(self):
        usr = _make_user(
            notification_prefs={
                "enabled": True,
                "cameras": {"cam-other": {"enabled": False}},
            }
        )
        svc, _, _ = _make_service(users=[usr])
        # Only update enabled — cameras dict shouldn't be touched.
        prefs, err = svc.update_prefs(user="alice", payload={"enabled": False})
        assert err == ""
        assert prefs["enabled"] is False
        assert prefs["cameras"]["cam-other"]["enabled"] is False
