# REQ: SWR-014; RISK: RISK-005; TEST: TC-019
"""Integration test: recording_mode="motion" → motion event → recorder.

Covers the Phase 4 wiring described in docs/archive/exec-plans/motion-detection.md:
a camera in motion mode should have its recorder started by the next
RecordingScheduler tick while a motion event is active, and stopped
after the post-roll grace window closes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from monitor.models import Camera, MotionEvent
from monitor.services.motion_event_store import MotionEventStore
from monitor.services.recording_scheduler import RecordingScheduler


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_scheduler(store, motion_store, post_roll=10.0):
    streaming = MagicMock()
    streaming.is_recording.return_value = False
    streaming.start_recorder.return_value = True

    control = MagicMock()
    control.start_stream.return_value = ({"state": "running"}, "")
    control.stop_stream.return_value = ({"state": "stopped"}, "")

    return RecordingScheduler(
        store=store,
        streaming=streaming,
        control_client=control,
        motion_event_store=motion_store,
        motion_post_roll_seconds=post_roll,
    )


def _cam_in_motion_mode(ip="192.0.2.10"):
    """Camera set to motion mode and already streaming — lets the
    scheduler skip the stream-start step and go straight to recorder."""
    return Camera(
        id="cam-motion-1",
        name="Front Door",
        status="online",
        ip=ip,
        recording_mode="motion",
        desired_stream_state="running",
        streaming=True,
    )


class TestMotionModeDrivesRecorder:
    def test_no_motion_no_recorder(self, app):
        """Motion mode with an empty store → no recording."""
        cam = _cam_in_motion_mode()
        app.store.save_camera(cam)

        sched = _make_scheduler(
            app.store, MotionEventStore(app.config["CONFIG_DIR"] + "/motion.json")
        )
        sched.tick()
        sched._streaming.start_recorder.assert_not_called()

    def test_in_progress_motion_event_starts_recorder(self, app, tmp_path):
        """Motion event with ``ended_at=None`` → recorder starts on next tick."""
        cam = _cam_in_motion_mode()
        app.store.save_camera(cam)

        motion_store = MotionEventStore(tmp_path / "motion.json")
        motion_store.append(
            MotionEvent(
                id="mot-in-progress",
                camera_id=cam.id,
                started_at=_iso(datetime.now(UTC)),
                ended_at=None,
                peak_score=0.1,
                peak_pixels_changed=1500,
                duration_seconds=0.0,
            )
        )

        sched = _make_scheduler(app.store, motion_store)
        sched.tick()

        sched._streaming.start_recorder.assert_called_once()
        args = sched._streaming.start_recorder.call_args
        assert args[0][0] == cam.id  # first positional arg = camera_id

    def test_recent_end_within_post_roll_keeps_recorder(self, app, tmp_path):
        """Event ended 5 s ago, post-roll 10 s → still wants recording."""
        cam = _cam_in_motion_mode()
        app.store.save_camera(cam)

        now = datetime.now(UTC)
        motion_store = MotionEventStore(tmp_path / "motion.json")
        motion_store.append(
            MotionEvent(
                id="mot-recent",
                camera_id=cam.id,
                started_at=_iso(now - timedelta(seconds=20)),
                ended_at=_iso(now - timedelta(seconds=5)),
                peak_score=0.12,
                peak_pixels_changed=2000,
                duration_seconds=15.0,
            )
        )

        sched = _make_scheduler(app.store, motion_store, post_roll=10.0)
        sched.tick()

        sched._streaming.start_recorder.assert_called_once()

    def test_old_end_outside_post_roll_stops_recorder(self, app, tmp_path):
        """Event ended 30 s ago, post-roll 10 s → recorder gets stopped."""
        cam = _cam_in_motion_mode()
        app.store.save_camera(cam)

        now = datetime.now(UTC)
        motion_store = MotionEventStore(tmp_path / "motion.json")
        motion_store.append(
            MotionEvent(
                id="mot-old",
                camera_id=cam.id,
                started_at=_iso(now - timedelta(seconds=60)),
                ended_at=_iso(now - timedelta(seconds=30)),
                peak_score=0.15,
                peak_pixels_changed=2500,
                duration_seconds=30.0,
            )
        )

        sched = _make_scheduler(app.store, motion_store, post_roll=10.0)
        # Pretend the recorder is currently running (from a previous
        # in-window tick) so the scheduler has something to stop.
        sched._streaming.is_recording.return_value = True

        sched.tick()

        sched._streaming.start_recorder.assert_not_called()
        sched._streaming.stop_recorder.assert_called_once_with(cam.id)

    def test_off_mode_ignores_motion_events(self, app, tmp_path):
        """recording_mode="off" must not record regardless of motion store."""
        cam = Camera(
            id="cam-off-1",
            name="Off cam",
            status="online",
            ip="192.0.2.11",
            recording_mode="off",
            desired_stream_state="running",
            streaming=True,
        )
        app.store.save_camera(cam)

        motion_store = MotionEventStore(tmp_path / "motion.json")
        motion_store.append(
            MotionEvent(
                id="mot-off",
                camera_id=cam.id,
                started_at=_iso(datetime.now(UTC)),
                ended_at=None,
            )
        )

        sched = _make_scheduler(app.store, motion_store)
        sched.tick()

        sched._streaming.start_recorder.assert_not_called()

    def test_continuous_mode_records_independent_of_motion(self, app, tmp_path):
        """Continuous stays on even if no motion events exist."""
        cam = Camera(
            id="cam-cont-1",
            name="Always on",
            status="online",
            ip="192.0.2.12",
            recording_mode="continuous",
            desired_stream_state="running",
            streaming=True,
        )
        app.store.save_camera(cam)

        motion_store = MotionEventStore(tmp_path / "motion.json")  # empty

        sched = _make_scheduler(app.store, motion_store)
        sched.tick()

        sched._streaming.start_recorder.assert_called_once()
