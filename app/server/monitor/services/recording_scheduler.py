"""
Recording scheduler (ADR-0017) — evaluates per-camera recording mode + schedule
once per minute and starts / stops recorder ffmpeg processes accordingly.

Policy (per tick, per camera):
    mode == "off"         → recorder stopped.
    mode == "continuous"  → recorder running while camera is paired.
    mode == "schedule"    → recorder running iff now-in-window(schedule, now).
    mode == "motion"      → treated as "off" (reserved for future motion ADR).

When turning a recorder on, the scheduler also asks the camera to start
streaming (if `desired_stream_state == "stopped"`), persists the new
desired state, and updates `cameras.json`.

When turning a recorder off, the scheduler defers the camera stop to the
on-demand coordinator (§6) which holds the single "does anything still
need this stream?" gate — so we don't fight with an active viewer.
"""

import logging
import threading
import time
from datetime import datetime
from datetime import time as dtime

log = logging.getLogger("monitor.recording_scheduler")

DAY_INDEX = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

TICK_INTERVAL_SECONDS = 60


def _parse_hhmm(s: str) -> dtime | None:
    """Parse a HH:MM string into a datetime.time, returning None on error."""
    try:
        hh, mm = s.split(":")
        return dtime(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def now_in_window(schedule: list[dict], now: datetime) -> bool:
    """Return True if `now` falls inside any window in `schedule`.

    Each window: {"days": [...], "start": "HH:MM", "end": "HH:MM"}.
    Overnight windows (end <= start) split into two halves:
        day D        from start to 24:00
        day D+1      from 00:00 to end
    """
    if not schedule:
        return False

    today_idx = now.weekday()
    yesterday_idx = (today_idx - 1) % 7
    current = now.time()

    for item in schedule:
        days = item.get("days") or []
        start = _parse_hhmm(item.get("start", ""))
        end = _parse_hhmm(item.get("end", ""))
        if start is None or end is None:
            continue

        day_keys = {d for d in days if d in DAY_INDEX}
        today_match = any(DAY_INDEX[d] == today_idx for d in day_keys)
        yest_match = any(DAY_INDEX[d] == yesterday_idx for d in day_keys)

        if end > start:
            # Same-day window [start, end).
            if today_match and start <= current < end:
                return True
        else:
            # Overnight — end <= start.
            # Day of record: start side (D), and spillover into D+1 morning.
            if today_match and current >= start:
                return True
            if yest_match and current < end:
                return True
    return False


class RecordingScheduler:
    """Background daemon that reconciles recording state once per minute."""

    def __init__(
        self,
        store,
        streaming,
        control_client,
        coordinator=None,
        tick_seconds: int = TICK_INTERVAL_SECONDS,
    ):
        self._store = store
        self._streaming = streaming
        self._control = control_client
        self._coordinator = coordinator  # may be None in tests
        self._tick = tick_seconds
        self._running = False
        self._thread: threading.Thread | None = None
        # Track cameras we currently think need the stream for recording.
        self._needed: set[str] = set()
        self._lock = threading.Lock()

    # --- Lifecycle --------------------------------------------------------

    def start(self):
        """Start the daemon tick loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name="recording-scheduler",
            daemon=True,
        )
        self._thread.start()
        log.info("RecordingScheduler started (tick=%ds)", self._tick)

    def stop(self):
        """Stop the daemon."""
        self._running = False

    # --- Public API -------------------------------------------------------

    def needs_stream(self, camera_id: str) -> bool:
        """True iff the scheduler currently wants this camera streaming."""
        with self._lock:
            return camera_id in self._needed

    def tick(self):
        """Run one reconciliation pass. Exposed for tests."""
        try:
            cameras = self._store.get_cameras()
        except Exception as exc:
            log.warning("Scheduler: failed to load cameras: %s", exc)
            return
        now = datetime.now()
        for cam in cameras:
            try:
                self._reconcile_camera(cam, now)
            except Exception as exc:
                log.warning("Scheduler reconcile failed for %s: %s", cam.id, exc)

    @staticmethod
    def evaluate(camera, now: datetime) -> bool:
        """Pure function — is recording wanted for this camera at `now`?

        Exposed for unit tests; no side-effects.
        """
        mode = getattr(camera, "recording_mode", "off")
        if mode == "continuous":
            return True
        if mode == "schedule":
            return now_in_window(getattr(camera, "recording_schedule", []) or [], now)
        # "off" and "motion" both map to False for MVP.
        return False

    # --- Internals --------------------------------------------------------

    def _run_loop(self):
        while self._running:
            self.tick()
            for _ in range(self._tick * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _reconcile_camera(self, camera, now: datetime) -> None:
        wanted = self.evaluate(camera, now)
        cam_id = camera.id

        is_recording = False
        if self._streaming is not None:
            is_recording = self._streaming.is_recording(cam_id)

        if wanted:
            with self._lock:
                self._needed.add(cam_id)

            # Ask the camera to stream if we haven't already.
            if (
                camera.desired_stream_state != "running"
                and camera.ip
                and self._control is not None
            ):
                _, err = self._control.start_stream(camera.ip)
                if err:
                    log.warning("Scheduler: start_stream(%s) failed: %s", cam_id, err)
                else:
                    camera.desired_stream_state = "running"
                    self._store.save_camera(camera)

            # Start the recorder if not already running.
            if not is_recording and self._streaming is not None:
                rtsp_url = f"rtsp://127.0.0.1:8554/{cam_id}"
                self._streaming.start_recorder(cam_id, rtsp_url)
        else:
            with self._lock:
                self._needed.discard(cam_id)

            if is_recording and self._streaming is not None:
                self._streaming.stop_recorder(cam_id)
                # After stopping, delegate the camera-stream-off decision to
                # the coordinator so we don't yank it out from under a viewer.
                self._maybe_request_stream_stop(cam_id)

    def _maybe_request_stream_stop(self, cam_id: str) -> None:
        """Ask the coordinator whether the camera stream can now be stopped."""
        if self._coordinator is None:
            return
        try:
            self._coordinator.stop(cam_id)
        except Exception as exc:
            log.debug("Coordinator stop(%s) failed: %s", cam_id, exc)
