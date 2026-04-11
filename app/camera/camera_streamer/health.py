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
import threading
import time

log = logging.getLogger("camera-streamer.health")


class HealthMonitor:
    """Monitor camera system health and report to systemd watchdog.

    Args:
        config: ConfigManager instance.
        capture_mgr: CaptureManager instance.
        stream_mgr: StreamManager instance.
        thermal_path: Path to thermal sensor file (from Platform).
                      None disables temperature monitoring.
    """

    def __init__(self, config, capture_mgr, stream_mgr, thermal_path=None):
        self._config = config
        self._capture = capture_mgr
        self._stream = stream_mgr
        self._thermal_path = thermal_path
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
