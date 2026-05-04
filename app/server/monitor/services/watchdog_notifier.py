# REQ: SWR-063; RISK: RISK-001, RISK-018; SEC: SC-019; TEST: TC-005, TC-044, TC-047
"""systemd watchdog notifier for the monitor server."""

import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("monitor.watchdog")

_DEFAULT_WATCHDOG_USEC = 30_000_000
_FAILURE_LOG_INTERVAL_SECONDS = 30.0
_STARTUP_GRACE_SECONDS = 30.0

READY = b"READY=1"
STOPPING = b"STOPPING=1"
WATCHDOG = b"WATCHDOG=1"


class WatchdogNotifier:
    """Probe the local server and only ping systemd on successful responses."""

    def __init__(
        self,
        probe_url: str,
        *,
        timeout_seconds: float = 2.0,
        clock=None,
        urlopen=urllib.request.urlopen,
    ):
        self._probe_url = probe_url
        self._timeout_seconds = timeout_seconds
        self._clock = clock or time.monotonic
        self._urlopen = urlopen
        self._watchdog_usec = self._read_watchdog_usec()
        self._interval_seconds = max(1, self._watchdog_usec // 3 // 1_000_000)
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()
        self._ready_sent = False
        self._stopping_sent = False
        self._disabled_logged = False
        self._last_failure_log_at = 0.0
        self._started_at = None
        self._last_beat_at = None

    @property
    def interval_seconds(self):
        return self._interval_seconds

    def start(self):
        """Start the background probe loop."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._started_at = self._clock()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="monitor-watchdog",
            )
            self._thread.start()

        if not os.environ.get("NOTIFY_SOCKET") and not self._disabled_logged:
            self._disabled_logged = True
            log.debug("liveness disabled: NOTIFY_SOCKET unset")

    def beat(self, component: str = "probe"):
        """Retain a public surface matching the camera notifier."""
        del component
        self._last_beat_at = self._clock()

    def mark_ready(self):
        """Send READY=1 exactly once."""
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
        """Stop the probe loop and optionally emit STOPPING=1."""
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

        ok, failure_reason = self._probe_once()
        if ok:
            self.beat()
            if not self._ready_sent:
                self.mark_ready()
                return
            self._notify(WATCHDOG)
            return

        if self._in_startup_grace():
            return

        self._log_probe_failure(failure_reason)

    def _probe_once(self):
        request = urllib.request.Request(self._probe_url, method="GET")
        try:
            with self._urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read()
                if response.status != 200:
                    return False, f"probe returned HTTP {response.status}"
                if body != b"ok\n":
                    return False, "probe returned unexpected body"
                return True, ""
        except urllib.error.HTTPError as exc:
            return False, f"probe returned HTTP {exc.code}"
        except urllib.error.URLError:
            return False, "probe timeout"
        except OSError:
            return False, "probe timeout"

    def _in_startup_grace(self):
        started_at = self._started_at
        if started_at is None:
            return False
        return (self._clock() - started_at) < _STARTUP_GRACE_SECONDS

    def _log_probe_failure(self, failure_reason: str):
        now = self._clock()
        with self._lock:
            if now - self._last_failure_log_at < _FAILURE_LOG_INTERVAL_SECONDS:
                return
            self._last_failure_log_at = now

        log.warning("liveness gate withheld: %s", failure_reason)

    def _notify(self, message: bytes):
        notify_socket = os.environ.get("NOTIFY_SOCKET")
        if not notify_socket:
            return

        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                if notify_socket.startswith("@"):
                    notify_socket = "\0" + notify_socket[1:]
                sock.connect(notify_socket)
                sock.sendall(message)
            finally:
                sock.close()
        except Exception:
            log.debug("sd_notify send failed", exc_info=True)
