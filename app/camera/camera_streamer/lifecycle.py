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
import ssl
import subprocess
import time
import urllib.error
import urllib.request

from camera_streamer import led
from camera_streamer.capture import CaptureManager
from camera_streamer.control import DEFAULT_STREAM_STATE_PATH, VALID_STREAM_STATES
from camera_streamer.discovery import DiscoveryService
from camera_streamer.health import HealthMonitor
from camera_streamer.heartbeat import HeartbeatSender
from camera_streamer.led import LedController
from camera_streamer.ota_agent import OTAAgent
from camera_streamer.pairing import PairingManager
from camera_streamer.status_server import CameraStatusServer
from camera_streamer.stream import StreamManager
from camera_streamer.wifi_setup import WifiSetupServer

log = logging.getLogger("camera-streamer.lifecycle")


def _read_desired_stream_state(path):
    """Return the persisted desired stream state or ``running``.

    Design: the camera streams to MediaMTX whenever it is paired so the
    Live page opens with zero cold-start latency. The persisted state
    file exists only as an explicit *override* — e.g. an operator or the
    server asks the camera to go quiet. A missing file (fresh boot,
    fresh pair) defaults to ``running``.

    Isolated as a module-level helper so the boot-time decision can be
    unit-tested without spinning up the full lifecycle.
    """
    try:
        with open(path) as f:
            value = f.read().strip()
    except OSError:
        return "running"
    if value in VALID_STREAM_STATES:
        return value
    return "running"


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
        # Persisted desired stream state file (ADR-0017). Stored on the
        # instance so tests can override and the heartbeat/control paths
        # share a single source of truth.
        self._stream_state_path = DEFAULT_STREAM_STATE_PATH

        self._state = State.INIT

        # Components — created lazily during lifecycle
        self._capture = None
        self._discovery = None
        self._stream = None
        self._status_server = None
        self._health = None
        self._heartbeat = None
        self._setup_server = None
        self._ota_agent = None
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

        if self._heartbeat:
            self._heartbeat.stop()
        if self._health:
            self._health.stop()
        if self._ota_agent:
            self._ota_agent.stop()
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

        # Start WiFi hotspot for setup (AP mode so user can connect)
        self._start_hotspot()

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

        log.info("Camera not paired — starting status server for /pair endpoint")
        led.setup_mode()

        # Start status server so /pair endpoint is accessible
        self._status_server = CameraStatusServer(
            self._config,
            stream_manager=None,
            wifi_interface=self._platform.wifi_interface,
            thermal_path=self._platform.thermal_path,
            pairing_manager=self._pairing,
        )
        self._status_server.start()

        # Poll until paired or shutdown
        last_registration_attempt = 0.0
        while not self._is_shutdown():
            now = time.monotonic()
            if now - last_registration_attempt >= 10:
                self._register_with_server()
                last_registration_attempt = now
            if self._pairing.is_paired:
                log.info("Pairing complete — certificates stored")
                self._status_server.stop()
                self._status_server = None
                return True
            time.sleep(2)

        self._status_server.stop()
        self._status_server = None
        return False

    def _do_connecting(self):
        """Wait for WiFi connectivity and resolve server address."""
        # Restore hostname on every boot (transient — lost on reboot)
        self._restore_hostname()

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
        # mDNS advertisement — pass pairing manager so TXT paired= reflects
        # real state (not just "has server IP"). Server-side discovery can
        # then prefer cameras advertising paired=false as pending candidates.
        self._discovery = DiscoveryService(self._config, pairing_manager=self._pairing)
        self._discovery.start()

        # RTSP streaming — on-demand per ADR-0017. The camera only starts
        # streaming on boot if both (a) it is configured/paired AND (b) the
        # persisted desired state is "running". A fresh camera defaults to
        # "stopped" and waits for the server to explicitly ask for a stream.
        self._stream = StreamManager(
            self._config,
            camera_device=self._platform.camera_device,
        )
        desired = _read_desired_stream_state(self._stream_state_path)
        if self._config.is_configured and desired == "running":
            log.info("Desired stream state is 'running' — starting pipeline")
            self._stream.start()
        elif not self._config.is_configured:
            log.warning("Server not configured — streaming disabled")
        else:
            log.info(
                "Desired stream state is '%s' — pipeline idle, awaiting "
                "server start command",
                desired,
            )

        # Status HTTPS server (port 443)
        self._status_server = CameraStatusServer(
            self._config,
            self._stream,
            wifi_interface=self._platform.wifi_interface,
            thermal_path=self._platform.thermal_path,
            pairing_manager=self._pairing,
            stream_state_path=self._stream_state_path,
        )
        self._status_server.start()

        # OTA update agent (port 8080)
        self._ota_agent = OTAAgent(self._config)
        self._ota_agent.start()

        # Heartbeat sender — keeps server informed of liveness (ADR-0016)
        # and reports the persisted desired stream state so the server can
        # detect drift (ADR-0017 §3). The control handler is the single
        # source of truth for desired state.
        self._heartbeat = HeartbeatSender(
            self._config,
            self._pairing,
            stream_manager=self._stream,
            thermal_path=self._platform.thermal_path,
            control_handler=self._status_server.control_handler,
        )
        if self._config.is_configured and self._pairing.is_paired:
            self._heartbeat.start()

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

    def _register_with_server(self):
        """Register this camera with the server as pending.

        Sends camera ID and IP so it appears in the server dashboard
        before pairing is complete. Best-effort — pairing still works
        if this fails (admin can add camera manually).
        """
        base_url = self._config.server_https_url
        if not base_url:
            return
        camera_id = self._config.camera_id
        if not camera_id:
            return
        url = f"{base_url}/api/v1/pair/register"
        try:
            import json

            data = json.dumps({"camera_id": camera_id}).encode()
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                log.info("Registered with server as pending (status=%d)", resp.status)
        except urllib.error.HTTPError as e:
            log.debug("Server registration rejected by %s: HTTP %s", url, e.code)
        except (urllib.error.URLError, OSError) as e:
            log.debug("Server registration failed for %s: %s", url, e)

    HOTSPOT_SCRIPT = "/opt/camera/scripts/camera-hotspot.sh"

    def _start_hotspot(self):
        """Start WiFi hotspot for setup mode if not already running.

        On Yocto images, camera-hotspot.service starts the hotspot
        via systemd before camera-streamer. This method checks if
        the hotspot is already active and only starts it if needed.
        Uses the same shell script as the systemd service (ADR-0013).
        """
        # Check if hotspot is already running (started by systemd)
        try:
            result = subprocess.run(
                [self.HOTSPOT_SCRIPT, "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                log.info("Hotspot already active (started by systemd)")
                return
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

        # Hotspot not running — start it
        try:
            result = subprocess.run(
                [self.HOTSPOT_SCRIPT, "start"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                log.info("Hotspot started via script")
            else:
                log.warning(
                    "Hotspot script failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip() or result.stdout.strip(),
                )
        except FileNotFoundError:
            log.warning("Hotspot script not found at %s", self.HOTSPOT_SCRIPT)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.warning("Hotspot script error: %s", e)

    def _restore_hostname(self):
        """Restore hostname from /data/config/hostname on every boot.

        The hostname is set transiently (memory-only) since rootfs is
        read-only. It was saved to /data during setup.
        """
        hostname_file = "/data/config/hostname"
        try:
            with open(hostname_file) as f:
                hostname = f.read().strip()
            if hostname:
                from camera_streamer import wifi

                wifi.set_hostname(hostname)
        except FileNotFoundError:
            log.debug("No saved hostname at %s", hostname_file)
        except Exception as e:
            log.warning("Failed to restore hostname: %s", e)

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
        """Remove setup stamp and exit so systemd respawns us into setup mode.

        SIGTERM-to-self rather than ``systemctl restart camera-streamer``:
        calling systemctl on our own unit from inside the unit is unreliable
        (D-Bus deadlocks on some systemd versions, silent no-op on BusyBox
        Yocto builds). Relying on ``Restart=always`` in the unit file is the
        portable pattern — see heartbeat._handle_server_unpair for details.
        """
        stamp = "/data/.setup-done"
        try:
            if os.path.isfile(stamp):
                os.remove(stamp)
                log.info("Removed %s — next boot enters setup wizard", stamp)
        except OSError as e:
            log.error("Failed to remove setup stamp: %s", e)

        log.info("Exiting (SIGTERM) so systemd restarts us into setup mode...")
        import signal as _signal

        try:
            os.kill(os.getpid(), _signal.SIGTERM)
        except OSError as e:
            log.error("Failed to send SIGTERM to self: %s", e)
