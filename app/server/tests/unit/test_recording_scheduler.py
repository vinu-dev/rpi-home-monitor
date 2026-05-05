# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Unit tests for RecordingScheduler (ADR-0017)."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from monitor.models import Camera
from monitor.services.recording_scheduler import (
    RecordingScheduler,
    now_in_window,
)


def _cam(mode="off", schedule=None, ip="192.0.2.5", desired="stopped", streaming=True):
    return Camera(
        id="cam-x",
        name="X",
        status="online",
        ip=ip,
        recording_mode=mode,
        recording_schedule=schedule or [],
        desired_stream_state=desired,
        streaming=streaming,
    )


class _FakeMotionStore:
    """Test stand-in for MotionEventStore — the scheduler only uses
    ``is_camera_active``; everything else on the real store would be
    noise here. Captures the last post_roll value so we can assert the
    scheduler forwards its config correctly."""

    def __init__(self, active=False):
        self.active = active
        self.last_post_roll = None

    def is_camera_active(self, camera_id, post_roll_seconds=None, now=None):
        self.last_post_roll = post_roll_seconds
        return self.active


class TestEvaluatePure:
    def test_off_never_records(self):
        cam = _cam("off")
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 17, 10, 0)) is False

    def test_continuous_always_records(self):
        cam = _cam("continuous")
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 17, 3, 0)) is True

    def test_motion_without_store_treated_as_off(self):
        """Back-compat: no motion_event_store wired → motion is still off.

        Matches the pre-Phase-4 behaviour so upgrading a deployment with
        existing recording_mode="motion" cameras can't suddenly start
        recording if the store isn't provisioned yet.
        """
        cam = _cam("motion")
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 17, 10, 0)) is False

    def test_motion_active_store_returns_true(self):
        cam = _cam("motion")
        store = _FakeMotionStore(active=True)
        assert (
            RecordingScheduler.evaluate(
                cam, datetime(2026, 4, 17, 10, 0), motion_event_store=store
            )
            is True
        )

    def test_motion_inactive_store_returns_false(self):
        cam = _cam("motion")
        store = _FakeMotionStore(active=False)
        assert (
            RecordingScheduler.evaluate(
                cam, datetime(2026, 4, 17, 10, 0), motion_event_store=store
            )
            is False
        )

    def test_motion_post_roll_passed_through(self):
        """Scheduler forwards post_roll to the store — the store is the
        authoritative timekeeper, the scheduler is just a router."""
        cam = _cam("motion")
        store = _FakeMotionStore(active=True)
        RecordingScheduler.evaluate(
            cam,
            datetime(2026, 4, 17, 10, 0),
            motion_event_store=store,
            motion_post_roll_seconds=42.0,
        )
        assert store.last_post_roll == 42.0

    def test_motion_store_exception_falls_back_to_inactive(self):
        """A broken store must not break the whole scheduler tick."""

        class _BrokenStore:
            def is_camera_active(self, *a, **k):
                raise RuntimeError("simulated")

        cam = _cam("motion")
        assert (
            RecordingScheduler.evaluate(
                cam, datetime(2026, 4, 17, 10, 0), motion_event_store=_BrokenStore()
            )
            is False
        )

    def test_continuous_ignores_motion_store(self):
        """Motion store only matters for mode=motion."""
        cam = _cam("continuous")
        store = _FakeMotionStore(active=False)
        assert (
            RecordingScheduler.evaluate(
                cam, datetime(2026, 4, 17, 10, 0), motion_event_store=store
            )
            is True
        )

    def test_schedule_in_window(self):
        # 2026-04-17 is a Friday.
        cam = _cam("schedule", [{"days": ["fri"], "start": "09:00", "end": "17:00"}])
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 17, 10, 30)) is True

    def test_schedule_out_of_window_time(self):
        cam = _cam("schedule", [{"days": ["fri"], "start": "09:00", "end": "17:00"}])
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 17, 8, 59)) is False
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 17, 17, 0)) is False

    def test_schedule_out_of_window_day(self):
        # Saturday is the 18th.
        cam = _cam("schedule", [{"days": ["fri"], "start": "09:00", "end": "17:00"}])
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 18, 10, 0)) is False

    def test_overnight_window_evaluates_correctly(self):
        # 22:00 Thursday → 06:00 Friday.
        cam = _cam("schedule", [{"days": ["thu"], "start": "22:00", "end": "06:00"}])
        # Thursday 23:00 (day record matches, post-start) → True
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 16, 23, 0)) is True
        # Friday 05:30 (day of record = Thursday, pre-end on spillover) → True
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 17, 5, 30)) is True
        # Friday 10:00 outside any recorded window → False
        assert RecordingScheduler.evaluate(cam, datetime(2026, 4, 17, 10, 0)) is False

    def test_now_in_window_empty_list(self):
        assert now_in_window([], datetime(2026, 4, 17, 10, 0)) is False


class TestReconcileSideEffects:
    """Per-tick reconciliation drives streaming + control + store."""

    @pytest.fixture
    def store(self, tmp_path):
        from monitor.store import Store

        return Store(str(tmp_path))

    def test_continuous_starts_recorder_and_stream(self, store):
        cam = _cam("continuous", desired="stopped")
        store.save_camera(cam)

        streaming = MagicMock()
        streaming.is_recording.return_value = False
        control = MagicMock()
        control.start_stream.return_value = ({"state": "running"}, "")

        sched = RecordingScheduler(store, streaming, control)
        sched.tick()

        control.start_stream.assert_called_once_with("192.0.2.5", camera_id="cam-x")
        streaming.start_recorder.assert_called_once()
        reloaded = store.get_camera("cam-x")
        assert reloaded.desired_stream_state == "running"
        assert sched.needs_stream("cam-x") is True

    def test_off_stops_recorder_and_asks_coordinator(self, store):
        cam = _cam("off", desired="running")
        store.save_camera(cam)

        streaming = MagicMock()
        streaming.is_recording.return_value = True
        control = MagicMock()
        coordinator = MagicMock()

        sched = RecordingScheduler(store, streaming, control, coordinator=coordinator)
        sched.tick()

        streaming.stop_recorder.assert_called_once_with("cam-x")
        coordinator.stop.assert_called_once_with("cam-x")
        assert sched.needs_stream("cam-x") is False

    def test_schedule_active_window_behaves_like_continuous(self, store, monkeypatch):
        # Force `now` to a Friday inside the window.
        cam = _cam(
            "schedule",
            schedule=[{"days": ["fri"], "start": "08:00", "end": "18:00"}],
            desired="stopped",
        )
        store.save_camera(cam)

        streaming = MagicMock()
        streaming.is_recording.return_value = False
        control = MagicMock()
        control.start_stream.return_value = ({"state": "running"}, "")

        sched = RecordingScheduler(store, streaming, control)

        class _FakeDT:
            @classmethod
            def now(cls):
                return datetime(2026, 4, 17, 12, 0)

        monkeypatch.setattr("monitor.services.recording_scheduler.datetime", _FakeDT)
        sched.tick()
        streaming.start_recorder.assert_called_once()

    def test_mode_change_mid_tick_flips(self, store):
        """Flipping recording_mode between ticks stops/starts the recorder."""
        cam = _cam("continuous", desired="stopped")
        store.save_camera(cam)

        streaming = MagicMock()
        streaming.is_recording.return_value = False
        control = MagicMock()
        control.start_stream.return_value = ({}, "")

        sched = RecordingScheduler(store, streaming, control)
        sched.tick()  # start
        streaming.start_recorder.assert_called_once()

        # Flip to off, pretend recorder is now running.
        cam = store.get_camera("cam-x")
        cam.recording_mode = "off"
        store.save_camera(cam)
        streaming.is_recording.return_value = True
        sched.tick()
        streaming.stop_recorder.assert_called_once()

    def test_recorder_not_started_until_camera_confirms_streaming(self, store):
        """Scheduler requests stream but defers recorder until heartbeat confirms."""
        cam = _cam("continuous", desired="stopped", streaming=False)
        store.save_camera(cam)

        streaming = MagicMock()
        streaming.is_recording.return_value = False
        control = MagicMock()
        control.start_stream.return_value = ({"state": "running"}, "")

        sched = RecordingScheduler(store, streaming, control)
        sched.tick()

        # Stream requested, but no recorder yet — camera hasn't confirmed.
        control.start_stream.assert_called_once()
        streaming.start_recorder.assert_not_called()

        # Next tick after heartbeat reports streaming=True → recorder starts.
        cam2 = store.get_camera("cam-x")
        cam2.streaming = True
        store.save_camera(cam2)
        sched.tick()
        streaming.start_recorder.assert_called_once()

    def test_no_control_call_when_already_running(self, store):
        cam = _cam("continuous", desired="running")
        store.save_camera(cam)

        streaming = MagicMock()
        streaming.is_recording.return_value = True
        control = MagicMock()

        sched = RecordingScheduler(store, streaming, control)
        sched.tick()

        control.start_stream.assert_not_called()
        # Recorder already running → no new start_recorder call either.
        streaming.start_recorder.assert_not_called()
