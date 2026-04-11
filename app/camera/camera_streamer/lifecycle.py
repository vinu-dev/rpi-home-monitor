"""
Camera lifecycle state machine — orchestrates startup, streaming, and shutdown.

States:
  INIT        → Load config, detect platform, configure LED
  SETUP       → First-boot WiFi hotspot + setup wizard (skipped if already done)
  PAIRING     → Wait for PIN-based pairing with server (skipped if already paired)
  CONNECTING  → Wait for WiFi IP, resolve server address via mDNS
  VALIDATING  → Check camera hardware (V4L2 device + H.264 support)
  RUNNING     → mDNS advertisement + RTSP streaming + health monitor + status server
  SHUTDOWN    → Graceful teardown of all services

Design patterns:
- Constructor Injection (config, platform injected)
- Single Responsibility (lifecycle orchestration only)
- Fail-Silent (hardware check failure doesn't block startup)
"""

import logging
import os
import socket
import subprocess
import time

from camera_streamer import led
from camera_streamer.capture import CaptureManager
from camera_streamer.discovery import DiscoveryService
from camera_streamer.health import HealthMonitor
from camera_streamer.led import LedController
from camera_streamer.pairing import PairingManager
from camera_streamer.status_server import CameraStatusServer
from camera_streamer.stream import StreamManager
from camera_streamer.wifi_setup import WifiSetupServer

log = logging.getLogger("camera-streamer.lifecycle")


class State:
    """Camera lifecycle states."""

    INIT = "init"
    SETUP = "setup"
    PAIRING = "pairing"
    CONNECTING = "connecting"
    VALIDATING = "validating"
    RUNNING = "running"
    SHUTDOWN = "shutdown"


class CameraLifecycle:
    """Orchestrates camera startup, streaming, and shutdown.

    Args:
        config: ConfigManager instance.
        platform: Platform instance (hardware paths).
        shutdown_event: Callable that returns True when shutdown requested.
    """

    WIFI_TIMEOUT = 60  # seconds to wait for WiFi IP

    def __init__(self, config, platform, shutdown_event):
        self._config = config
        self._platform = platform
        self._is_shutdown = shutdown_event

        self._state = State.INIT

        # Components — created lazily during lifecycle
        self._capture = None
        self._discovery = None
        self._stream = None
        self._status_server = None
        self._health = None
        self._setup_server = None
        self._pairing = PairingManager(config)

    @property
    def state(self):
        return self._state

    def run(self):
        """Execute the full lifecycle. Returns when shutdown is requested."""
        transitions = [
            (State.INIT, self._do_init),
            (State.SETUP, self._do_setup),
            (State.PAIRING, self._do_pairing),
            (State.CONNECTING, self._do_connecting),
            (State.VALIDATING, self._do_validating),
            (State.RUNNING, self._do_running),
        ]

        for state, handler in transitions:
            if self._is_shutdown():
                break
            self._state = state
            log.info("State → %s", state)

            ok = handler()
            if not ok:
                log.warning("State %s returned early — entering shutdown", state)
                break

        self.shutdown()

    def shutdown(self):
        """Graceful teardown of all services."""
        self._state = State.SHUTDOWN
        log.info("State → shutdown")

        if self._health:
            self._health.stop()
        if self._stream:
            self._stream.stop()
        if self._status_server:
            self._status_server.stop()
        if self._discovery:
            self._discovery.stop()

        log.info("Camera streamer stopped.")

    # ---- State handlers ----

    def _do_init(self):
        """Load config, detect platform, configure LED."""
        led.set_controller(LedController(self._platform.led_path))

        log.info(
            "Platform: camera=%s wifi=%s led=%s thermal=%s",
            self._platform.camera_device,
            self._platform.wifi_interface,
            self._platform.led_path or "none",
            self._platform.thermal_path or "none",
        )
        return True

    def _do_setup(self):
        """Run first-boot setup wizard if needed."""
        self._setup_server = WifiSetupServer(
            self._config,
            wifi_interface=self._platform.wifi_interface,
            hostname_prefix=self._platform.hostname_prefix,
        )

        if not self._setup_server.needs_setup():
            log.debug("Setup already complete, skipping")
            return True

        log.info("First boot — starting setup wizard")
        self._setup_server.start()

        while not self._is_shutdown() and self._setup_server.needs_setup():
            time.sleep(1)

        self._setup_server.stop()

        if self._is_shutdown():
            return False

        # Reload config after setup completes
        self._config.load()
        log.info("Setup complete, config reloaded")
        return True

    def _do_pairing(self):
        """Wait for pairing if camera has no client certificate.

        If already paired (client.crt exists), skip immediately.
        Otherwise, the status server's /pair page allows the admin
        to enter the PIN shown on the server dashboard.
        """
        if self._pairing.is_paired:
            log.info("Camera already paired — skipping pairing state")
            return True

        log.info("Camera not paired — waiting for pairing via status page /pair")
        led.setup_mode()

        # Poll until paired or shutdown
        while not self._is_shutdown():
            if self._pairing.is_paired:
                log.info("Pairing complete — certificates stored")
                return True
            time.sleep(2)

        return False

    def _do_connecting(self):
        """Wait for WiFi connectivity and resolve server address."""
        if not self._wait_for_wifi():
            log.error(
                "WiFi has no IP after %ds — reverting to setup mode", self.WIFI_TIMEOUT
            )
            self._revert_to_setup()
            return False

        if self._config.is_configured:
            self._resolve_server()

        return True

    def _do_validating(self):
        """Validate camera hardware (V4L2 device)."""
        log.info("--- Camera Hardware Check ---")
        self._capture = CaptureManager(device=self._platform.camera_device)
        if not self._capture.check():
            log.error(
                "Camera device not available. Troubleshooting:\n"
                "  1. Check ribbon cable is seated firmly\n"
                "  2. Check config.txt has: start_x=1 and gpu_mem=128\n"
                "  3. For PiHut ZeroCam (OV5647): dtoverlay=ov5647\n"
                "  4. Run: vcgencmd get_camera\n"
                "Will retry via health monitor..."
            )
        else:
            log.info(
                "Camera hardware OK: device=%s h264=%s",
                self._capture.device,
                self._capture.supports_h264(),
            )

        # Don't fail — health monitor will retry
        return True

    def _do_running(self):
        """Start all runtime services and enter main loop."""
        # mDNS advertisement
        self._discovery = DiscoveryService(self._config)
        self._discovery.start()

        # RTSP streaming
        self._stream = StreamManager(
            self._config,
            camera_device=self._platform.camera_device,
        )
        if self._config.is_configured:
            self._stream.start()
        else:
            log.warning("Server not configured — streaming disabled")

        # Status HTTP server (port 80)
        self._status_server = CameraStatusServer(
            self._config,
            self._stream,
            wifi_interface=self._platform.wifi_interface,
            thermal_path=self._platform.thermal_path,
            pairing_manager=self._pairing,
        )
        self._status_server.start()

        # Health monitoring
        self._health = HealthMonitor(
            self._config,
            self._capture,
            self._stream,
            thermal_path=self._platform.thermal_path,
        )
        self._health.start()

        led.connected()
        log.info("Camera streamer running (camera=%s)", self._config.camera_id)

        # Main loop — wait for shutdown
        while not self._is_shutdown():
            time.sleep(1)

        return True

    # ---- Helper methods ----

    def _wait_for_wifi(self):
        """Wait for WiFi interface to have an IP address."""
        iface = self._platform.wifi_interface
        log.info(
            "Checking WiFi connectivity on %s (timeout=%ds)...",
            iface,
            self.WIFI_TIMEOUT,
        )

        for elapsed in range(self.WIFI_TIMEOUT):
            if self._is_shutdown():
                return True  # Don't block shutdown

            try:
                result = subprocess.run(
                    ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", iface],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                for line in result.stdout.strip().splitlines():
                    if line.startswith("IP4.ADDRESS") and "/" in line:
                        ip = line.split(":", 1)[1].split("/")[0]
                        if ip and ip != "0.0.0.0":
                            log.info("WiFi connected with IP %s after %ds", ip, elapsed)
                            return True
            except Exception:
                pass
            time.sleep(1)

        log.warning("No WiFi IP on %s after %ds", iface, self.WIFI_TIMEOUT)
        return False

    def _resolve_server(self):
        """Resolve server address — handles mDNS names like homemonitor.local."""
        addr = self._config.server_ip
        if not addr:
            return
        try:
            ip = socket.gethostbyname(addr)
            log.info("Server address resolved: %s -> %s", addr, ip)
        except socket.gaierror:
            log.warning(
                "Cannot resolve server address '%s' — mDNS may not be ready. "
                "Will retry when streaming starts.",
                addr,
            )

    @staticmethod
    def _revert_to_setup():
        """Remove setup stamp and restart service for setup wizard."""
        stamp = "/data/.setup-done"
        try:
            if os.path.isfile(stamp):
                os.remove(stamp)
                log.info("Removed %s — next boot enters setup wizard", stamp)
        except OSError as e:
            log.error("Failed to remove setup stamp: %s", e)

        log.info("Restarting camera-streamer service to enter setup mode...")
        try:
            subprocess.run(
                ["systemctl", "restart", "camera-streamer"],
                capture_output=True,
                timeout=10,
            )
        except Exception as e:
            log.error("Failed to restart service: %s", e)
