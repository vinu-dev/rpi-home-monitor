# REQ: SWR-015; RISK: RISK-005; SEC: SC-004; TEST: TC-008
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

Readiness contract (issue #198): ``avahi-publish-*`` exits with a
non-zero return code immediately if avahi-daemon is not yet available
on the bus, or if the publication is rejected (duplicate name,
malformed TXT, etc.). Without a brief post-launch readiness check,
``Popen`` returns successfully and the service believes it is
advertising while in reality nothing reaches the wire — the exact
"silent green" failure mode operators reported on cold boot. We poll
``process.poll()`` for a short window after launch and surface any
immediate failure (with the child's stderr) instead of pretending all
is well.
"""

import logging
import subprocess
import time

from camera_streamer import wifi

log = logging.getLogger("camera-streamer.discovery")

SERVICE_TYPE = "_rtsp._tcp"
SERVICE_PORT = 8554
VERSION = "1.0.0"


class DiscoveryService:
    """Advertise camera via mDNS/Avahi for server auto-discovery."""

    # Total wall-time we wait for an avahi-publish-* helper to confirm
    # it didn't immediately exit. avahi's failure modes (bus not ready,
    # duplicate name, bad TXT) all produce an exit within tens of
    # milliseconds, so 500 ms is comfortably above the noise floor.
    # Tests override this to 0.0 (see camera test conftest) so the suite
    # does not pay a half-second per start() call.
    PUBLISH_READINESS_TIMEOUT_SECONDS = 0.5
    # Cadence of poll() checks within the readiness window. Smaller than
    # the timeout so we detect failures quickly without burning a CPU
    # core on a tight loop.
    PUBLISH_READINESS_POLL_INTERVAL = 0.05

    def __init__(self, config, pairing_manager=None):
        self._config = config
        self._pairing = pairing_manager
        self._process = None
        self._host_process = None
        self._running = False

    @property
    def is_advertising(self):
        return self._process is not None and self._process.poll() is None

    def start(self):
        """Start mDNS advertisement.

        After spawning the avahi-publish helper we briefly verify the
        process is still alive — the helper exits immediately if
        avahi-daemon isn't ready or the publication is rejected, and
        we'd otherwise log "advertisement started" while nothing was
        on the wire. See module docstring (issue #198).
        """
        if self._running:
            return

        self._running = True
        camera_id = self._config.camera_id
        # "paired" reflects true pairing state (client cert on disk), not
        # just "server IP configured". Fixes the sync bug where a camera
        # that was unpaired by the server kept advertising paired=true.
        is_paired = bool(self._pairing and self._pairing.is_paired)
        paired = "true" if is_paired else "false"
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
        except FileNotFoundError:
            log.error("avahi-publish-service not found — mDNS disabled")
            self._running = False
            self._process = None
            return
        except OSError as e:
            log.error("Failed to start mDNS: %s", e)
            self._running = False
            self._process = None
            return

        if not self._verify_publish_alive(self._process, "service"):
            # _verify_publish_alive logged the actual failure with stderr.
            # Drop the dead handle so is_advertising returns False and the
            # outer watchdog can retry on the next supervision tick.
            self._process = None
            self._running = False
            return

        log.info(
            "mDNS advertisement started: %s %s port %d",
            service_name,
            SERVICE_TYPE,
            SERVICE_PORT,
        )
        self._start_host_advertisement()

    def _start_host_advertisement(self):
        """Publish the unique camera hostname as an mDNS A record."""
        hostname = wifi.get_hostname()
        ip_address = wifi.get_ip_address()
        if not hostname or not ip_address:
            log.warning(
                "Skipping hostname mDNS advertisement (hostname=%s ip=%s)",
                hostname or "(missing)",
                ip_address or "(missing)",
            )
            return

        host_label = hostname if hostname.endswith(".local") else f"{hostname}.local"
        cmd = [
            "avahi-publish-address",
            "-R",
            "-f",
            host_label,
            ip_address,
        ]

        try:
            self._host_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            log.warning("avahi-publish-address not found — hostname mDNS disabled")
            return
        except OSError as e:
            log.warning("Failed to start hostname mDNS advertisement: %s", e)
            return

        if not self._verify_publish_alive(self._host_process, "host"):
            # Service publication is still good — operators can still find
            # us by service browse, they just lose the cam-id.local A
            # record. Don't fail the whole start() here.
            self._host_process = None
            return

        log.info(
            "mDNS hostname advertisement started: %s -> %s", host_label, ip_address
        )

    def _verify_publish_alive(self, process, label):
        """Confirm an avahi-publish-* helper survived the first window.

        Returns True if the process is still alive at the deadline.
        Returns False if it exited within the window — the failure is
        logged at ERROR with the captured stderr (or ``<no stderr>``)
        and the caller is expected to drop the handle.

        ``label`` is the short descriptor used in log lines to
        distinguish service-browse failures from hostname-A-record
        failures (e.g. ``"service"``, ``"host"``).
        """
        deadline = time.monotonic() + self.PUBLISH_READINESS_TIMEOUT_SECONDS
        while True:
            rc = process.poll()
            if rc is not None:
                stderr_text = ""
                try:
                    if process.stderr is not None:
                        stderr_bytes = process.stderr.read() or b""
                        stderr_text = stderr_bytes.decode(errors="replace").strip()
                except (OSError, ValueError):
                    # stderr already closed / not a real pipe (test mock).
                    pass
                log.error(
                    "avahi-publish-%s exited immediately (rc=%d): %s",
                    label,
                    rc,
                    stderr_text or "<no stderr>",
                )
                return False
            if time.monotonic() >= deadline:
                return True
            time.sleep(self.PUBLISH_READINESS_POLL_INTERVAL)

    def stop(self):
        """Stop mDNS advertisement."""
        self._running = False
        for attr_name in ["_process", "_host_process"]:
            process = getattr(self, attr_name)
            if process is None:
                continue
            try:
                process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
            setattr(self, attr_name, None)
        if self._process is None and self._host_process is None:
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
