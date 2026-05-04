"""
Health monitoring for camera-streamer.

Reports system health metrics and notifies systemd watchdog.
Checks:
- Camera device accessible
- ffmpeg process alive
- Network connectivity to server
- Disk space on /data
- CPU temperature (via injectable thermal_path from Platform)
"""

import logging
import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime

log = logging.getLogger("camera-streamer.health")

_THROTTLE_BITS = {
    "under_voltage_now": 0,
    "frequency_capped_now": 1,
    "throttled_now": 2,
    "soft_temp_limit_now": 3,
    "under_voltage_sticky": 16,
    "frequency_capped_sticky": 17,
    "throttled_sticky": 18,
    "soft_temp_limit_sticky": 19,
}
_THROTTLE_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")


def _decode_throttle_state(raw_value: int, *, source: str) -> dict:
    state = {key: bool(raw_value & (1 << bit)) for key, bit in _THROTTLE_BITS.items()}
    state["last_updated"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["raw_value_hex"] = f"0x{raw_value:08x}"
    state["source"] = source
    return state


def _parse_throttle_value(raw: str) -> int | None:
    if not isinstance(raw, str):
        return None
    match = _THROTTLE_HEX_RE.search(raw)
    if match:
        try:
            return int(match.group(0), 16)
        except ValueError:
            return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


def read_throttle_state(
    vcgencmd_path: str | None = None, throttle_path: str | None = None
) -> dict | None:
    """Read Raspberry Pi throttle bits via vcgencmd, then sysfs fallback."""
    if vcgencmd_path:
        try:
            result = subprocess.run(
                [vcgencmd_path, "get_throttled"],
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            raw_value = _parse_throttle_value(result.stdout or result.stderr or "")
            if raw_value is not None:
                return _decode_throttle_state(raw_value, source="vcgencmd")

    if throttle_path:
        try:
            with open(throttle_path) as f:
                raw_value = _parse_throttle_value(f.read())
        except OSError:
            raw_value = None
        if raw_value is not None:
            return _decode_throttle_state(raw_value, source="sysfs")

    return None


# REQ: SWR-037; RISK: RISK-022; TEST: TC-035
class HealthMonitor:
    """Monitor camera system health and report to systemd watchdog.

    Args:
        config: ConfigManager instance.
        capture_mgr: CaptureManager instance.
        stream_mgr: StreamManager instance.
        thermal_path: Path to thermal sensor file (from Platform).
                      None disables temperature monitoring.
    """

    def __init__(
        self,
        config,
        capture_mgr,
        stream_mgr,
        thermal_path=None,
        vcgencmd_path=None,
        throttle_path=None,
    ):
        self._config = config
        self._capture = capture_mgr
        self._stream = stream_mgr
        self._thermal_path = thermal_path
        self._vcgencmd_path = vcgencmd_path
        self._throttle_path = throttle_path
        self._last_throttle_state = None
        self._running = False
        self._thread = None
        self._interval = 15  # seconds between health checks

    @property
    def is_running(self):
        return self._running

    def start(self):
        """Start health monitoring loop."""
        self._running = True
        self._thread = threading.Thread(
            target=self._health_loop, daemon=True, name="health-monitor"
        )
        self._thread.start()
        log.info("Health monitor started (interval=%ds)", self._interval)

    def stop(self):
        """Stop health monitoring."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        log.info("Health monitor stopped")

    def get_status(self):
        """Return current health status dict."""
        return {
            "camera_available": self._capture.available,
            "streaming": self._stream.is_streaming,
            "server_configured": self._config.is_configured,
            "camera_id": self._config.camera_id,
            "cpu_temp": self.read_cpu_temp(),
            "disk_free_mb": _get_disk_free_mb(self._config.data_dir),
            "throttle_state": self.read_throttle_state(),
        }

    def read_cpu_temp(self):
        """Read CPU temperature from the configured thermal sensor."""
        if not self._thermal_path:
            return None
        try:
            with open(self._thermal_path) as f:
                return int(f.read().strip()) / 1000.0
        except (OSError, ValueError):
            return None

    def read_throttle_state(self):
        """Return the most recent throttle-state sample, retaining last good."""
        state = read_throttle_state(self._vcgencmd_path, self._throttle_path)
        if state is not None:
            self._last_throttle_state = state
        return self._last_throttle_state

    def _health_loop(self):
        """Periodic health check loop."""
        while self._running:
            try:
                self._run_check()
                self._notify_watchdog()
            except Exception:
                log.exception("Health check error")

            # Sleep in small increments for responsive shutdown
            for _ in range(self._interval * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _run_check(self):
        """Run a single health check cycle."""
        status = self.get_status()

        if not status["camera_available"]:
            log.warning("Camera device not available")
        if self._config.is_configured and not status["streaming"]:
            log.warning("Stream not active (server=%s)", self._config.server_ip)

        temp = status["cpu_temp"]
        if temp and temp > 80.0:
            log.warning("CPU temperature high: %.1f C", temp)

        disk = status["disk_free_mb"]
        if disk is not None and disk < 50:
            log.warning("Low disk space: %d MB free", disk)

    def _notify_watchdog(self):
        """Send systemd watchdog notification."""
        try:
            import socket

            addr = os.environ.get("NOTIFY_SOCKET")
            if not addr:
                return
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                if addr.startswith("@"):
                    addr = "\0" + addr[1:]
                sock.connect(addr)
                sock.sendall(b"WATCHDOG=1")
            finally:
                sock.close()
        except Exception:
            pass  # Watchdog notification is best-effort


def _get_disk_free_mb(path):
    """Get free disk space in MB for a given path."""
    try:
        stat = os.statvfs(path)
        return (stat.f_bavail * stat.f_frsize) // (1024 * 1024)
    except OSError:
        return None
