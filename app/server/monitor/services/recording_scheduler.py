"""
Recording scheduler (ADR-0017) — evaluates per-camera recording mode + schedule
once per minute and starts / stops recorder ffmpeg processes accordingly.

Policy (per tick, per camera):
    mode == "off"         → recorder stopped.
    mode == "continuous"  → recorder running while camera is paired.
    mode == "schedule"    → recorder running iff now-in-window(schedule, now).
    mode == "motion"      → recorder running iff a motion event is currently
                            in progress, OR within `motion_post_roll_seconds`
                            of its end. Requires a `motion_event_store` to be
                            wired in (see ADR-0021); without one, motion mode
                            silently evaluates to False — the same fail-safe
                            pre-Phase-4 behaviour the docstring used to claim
                            was the permanent design.

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

from monitor.services.time_window import now_in_window

log = logging.getLogger("monitor.recording_scheduler")

TICK_INTERVAL_SECONDS = 60


class RecordingScheduler:
    """Background daemon that reconciles recording state once per minute."""

    def __init__(
        self,
        store,
        streaming,
        control_client,
        coordinator=None,
        tick_seconds: int = TICK_INTERVAL_SECONDS,
        motion_event_store=None,
        motion_post_roll_seconds: float = 10.0,
    ):
        self._store = store
        self._streaming = streaming
        self._control = control_client
        self._coordinator = coordinator  # may be None in tests
        self._tick = tick_seconds
        # Optional motion wiring — when present, recording_mode="motion"
        # evaluates to True while a motion event is in progress or within
        # the post-roll grace window. Absent (e.g., tests that only care
        # about continuous/schedule), motion mode is a silent no-op.
        self._motion_event_store = motion_event_store
        self._motion_post_roll_seconds = float(motion_post_roll_seconds)
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

    def nudge(self, camera_id: str) -> None:
        """Reconcile a single camera *right now*, bypassing the tick loop.

        The periodic tick is 60 s by default (fine for continuous /
        schedule modes where recording windows are minutes long). For
        motion mode, typical events are 3-10 s so a 60 s poll misses
        the window entirely — by the time the scheduler notices, both
        motion and its post-roll are over. Call this when a new motion
        event arrives to start the recorder without waiting for the
        next tick. Safe from any thread; best-effort.
        """
        try:
            camera = self._store.get_camera(camera_id)
        except Exception as exc:
            log.debug(
                "Scheduler nudge: store.get_camera(%s) failed: %s", camera_id, exc
            )
            return
        if camera is None:
            return
        try:
            self._reconcile_camera(camera, datetime.now())
        except Exception as exc:
            log.warning("Scheduler nudge reconcile failed for %s: %s", camera_id, exc)

    @staticmethod
    def evaluate(
        camera,
        now: datetime,
        motion_event_store=None,
        motion_post_roll_seconds: float = 10.0,
    ) -> bool:
        """Pure function — is recording wanted for this camera at `now`?

        Exposed for unit tests; no side-effects.

        For ``recording_mode = "motion"``, a ``motion_event_store`` must be
        provided (otherwise motion silently evaluates to False, the
        pre-Phase-4 behaviour). The store's ``is_camera_active`` is the
        single source of truth — a motion event is "active" while it's
        in progress or within ``motion_post_roll_seconds`` of its end.
        """
        # REQ: SWR-005; RISK: RISK-001; TEST: TC-002
        mode = getattr(camera, "recording_mode", "off")
        if mode == "continuous":
            return True
        if mode == "schedule":
            return now_in_window(getattr(camera, "recording_schedule", []) or [], now)
        if mode == "motion":
            if motion_event_store is None:
                return False
            try:
                return bool(
                    motion_event_store.is_camera_active(
                        camera.id,
                        post_roll_seconds=motion_post_roll_seconds,
                    )
                )
            except Exception as exc:
                log.warning(
                    "motion evaluate failed for %s: %s — treating as inactive",
                    camera.id,
                    exc,
                )
                return False
        # "off" → False.
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
        wanted = self.evaluate(
            camera,
            now,
            motion_event_store=self._motion_event_store,
            motion_post_roll_seconds=self._motion_post_roll_seconds,
        )
        cam_id = camera.id

        is_recording = False
        if self._streaming is not None:
            is_recording = self._streaming.is_recording(cam_id)

        if wanted:
            with self._lock:
                self._needed.add(cam_id)

            # Ask the camera to stream if we haven't already.
            stream_ready = camera.desired_stream_state == "running"
            if not stream_ready and camera.ip and self._control is not None:
                _, err = self._control.start_stream(camera.ip)
                if err:
                    log.warning("Scheduler: start_stream(%s) failed: %s", cam_id, err)
                else:
                    camera.desired_stream_state = "running"
                    self._store.save_camera(camera)
                    stream_ready = True

            # Start the recorder only once the camera stream is actually
            # running — otherwise ffmpeg would spin in a dead-restart loop
            # against a source that has no publisher.
            if (
                stream_ready
                and getattr(camera, "streaming", False)
                and not is_recording
                and self._streaming is not None
            ):
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
