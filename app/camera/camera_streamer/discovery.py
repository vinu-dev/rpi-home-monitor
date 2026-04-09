"""
mDNS service advertisement via Avahi.

Advertises the camera on the local network so the server
can auto-discover it.

Service: _rtsp._tcp
TXT records:
  id       = cam-<hardware-serial>
  version  = firmware version
  resolution = 1080p
  paired   = true/false

Uses avahi-publish-service which is part of avahi-daemon package.
"""
import subprocess
import threading
import logging
import os

log = logging.getLogger("camera-streamer.discovery")

SERVICE_TYPE = "_rtsp._tcp"
SERVICE_PORT = 8554
VERSION = "1.0.0"


class DiscoveryService:
    """Advertise camera via mDNS/Avahi for server auto-discovery."""

    def __init__(self, config):
        self._config = config
        self._process = None
        self._running = False

    @property
    def is_advertising(self):
        return self._process is not None and self._process.poll() is None

    def start(self):
        """Start mDNS advertisement."""
        if self._running:
            return

        self._running = True
        camera_id = self._config.camera_id
        paired = "true" if self._config.is_configured else "false"
        resolution = f"{self._config.width}x{self._config.height}"

        # avahi-publish-service runs in foreground — keeps advertising
        # until killed
        service_name = f"HomeMonitor Camera ({camera_id})"
        cmd = [
            "avahi-publish-service",
            service_name,
            SERVICE_TYPE,
            str(SERVICE_PORT),
            f"id={camera_id}",
            f"version={VERSION}",
            f"resolution={resolution}",
            f"paired={paired}",
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            log.info(
                "mDNS advertisement started: %s %s port %d",
                service_name,
                SERVICE_TYPE,
                SERVICE_PORT,
            )
        except FileNotFoundError:
            log.error("avahi-publish-service not found — mDNS disabled")
            self._running = False
        except OSError as e:
            log.error("Failed to start mDNS: %s", e)
            self._running = False

    def stop(self):
        """Stop mDNS advertisement."""
        self._running = False
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._process.kill()
                except OSError:
                    pass
            self._process = None
            log.info("mDNS advertisement stopped")

    def update_paired_status(self, paired):
        """Restart advertisement with updated paired status."""
        if self._running:
            self.stop()
        # Short delay to let avahi clean up
        import time
        time.sleep(0.5)
        self._running = False  # Reset so start() works
        self.start()
