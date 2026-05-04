# REQ: SWR-062; RISK: RISK-001, RISK-008; TEST: TC-005, TC-047
"""systemd watchdog notifier for the camera runtime."""

import logging
import os
import threading
import time

from camera_streamer.sd_notify import READY, STOPPING, WATCHDOG, notify

log = logging.getLogger("camera-streamer.watchdog")

_DEFAULT_WATCHDOG_USEC = 30_000_000
_WITHHELD_LOG_INTERVAL_SECONDS = 10.0


class WatchdogNotifier:
    """Gate systemd watchdog pings on camera lifecycle progress."""

    def __init__(
        self,
        *,
        gate_components=("lifecycle", "setup", "pairing", "connecting"),
        clock=None,
        notify_func=notify,
    ):
        self._clock = clock or time.monotonic
        self._notify = notify_func
        self._gate_components = set(gate_components)
        self._watchdog_usec = self._read_watchdog_usec()
        self._interval_seconds = max(1, self._watchdog_usec // 3 // 1_000_000)
        self._freshness_seconds = self._watchdog_usec / 1_000_000 * 0.5
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()
        self._beats = {}
        self._ready_sent = False
        self._stopping_sent = False
        self._disabled_logged = False
        self._last_withheld_log_at = 0.0

    @property
    def interval_seconds(self):
        return self._interval_seconds

    @property
    def watchdog_usec(self):
        return self._watchdog_usec

    def start(self):
        """Start the watchdog thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="camera-watchdog",
            )
            self._thread.start()

        if not os.environ.get("NOTIFY_SOCKET") and not self._disabled_logged:
            self._disabled_logged = True
            log.debug("liveness disabled: NOTIFY_SOCKET unset")

    def beat(self, component: str = "lifecycle"):
        """Record forward progress for a named component."""
        with self._lock:
            self._beats[component] = self._clock()

    def mark_ready(self):
        """Send READY=1 exactly once once the service is genuinely alive."""
        if not os.environ.get("NOTIFY_SOCKET"):
            return

        should_send = False
        with self._lock:
            if not self._ready_sent:
                self._ready_sent = True
                should_send = True

        if should_send:
            self._notify(READY)
            log.info("liveness ready")

    def stop(self, stopping: bool = True):
        """Stop the watchdog thread and optionally send STOPPING=1."""
        should_send_stopping = False
        with self._lock:
            if stopping and self._ready_sent and not self._stopping_sent:
                self._stopping_sent = True
                should_send_stopping = True

        if should_send_stopping and os.environ.get("NOTIFY_SOCKET"):
            self._notify(STOPPING)
            log.info("liveness shutting down")

        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(1, self._interval_seconds))

    def _read_watchdog_usec(self):
        raw_value = os.environ.get("WATCHDOG_USEC")
        if raw_value:
            try:
                value = int(raw_value)
                if value > 0:
                    return value
            except ValueError:
                pass

        log.info("WATCHDOG_USEC unset; defaulting liveness interval to 30s")
        return _DEFAULT_WATCHDOG_USEC

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("watchdog notifier loop crashed")

            if self._stop_event.wait(self._interval_seconds):
                return

    def _tick(self):
        if not os.environ.get("NOTIFY_SOCKET"):
            return

        with self._lock:
            if not self._ready_sent:
                return
            now = self._clock()
            fresh_component = self._fresh_component(now)

        if fresh_component is None:
            self._log_withheld(now)
            return

        self._notify(WATCHDOG)

    def _fresh_component(self, now: float) -> str | None:
        freshest_name = None
        freshest_at = None
        for name in self._gate_components:
            beat_at = self._beats.get(name)
            if beat_at is None:
                continue
            if now - beat_at >= self._freshness_seconds:
                continue
            if freshest_at is None or beat_at > freshest_at:
                freshest_name = name
                freshest_at = beat_at
        return freshest_name

    def _log_withheld(self, now: float):
        with self._lock:
            if now - self._last_withheld_log_at < _WITHHELD_LOG_INTERVAL_SECONDS:
                return
            beat_times = [
                beat_at
                for beat_at in (self._beats.get(name) for name in self._gate_components)
                if beat_at is not None
            ]
            freshest = max(beat_times, default=None)
            self._last_withheld_log_at = now

        if freshest is None:
            log.info("liveness gate withheld: no progress beat recorded")
            return

        age_seconds = now - freshest
        log.info("liveness gate withheld: lifecycle stale (age=%.1fs)", age_seconds)
