# REQ: SWR-012, SWR-062; RISK: RISK-001, RISK-008; TEST: TC-005, TC-018
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
import threading
import time
import urllib.error
import urllib.request

from camera_streamer import led
from camera_streamer.capture import CaptureManager
from camera_streamer.control import DEFAULT_STREAM_STATE_PATH, VALID_STREAM_STATES
from camera_streamer.discovery import DiscoveryService
from camera_streamer.faults import (
    FAULT_NETWORK_MDNS_RESOLUTION_FAILED,
    make_fault,
)
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
    """Return the persisted desired stream state or ``stopped``.

    Design (ADR-0017, issue #115): the camera is on-demand — it does not
    stream until the server explicitly asks it to. A missing persisted
    state file (fresh boot, fresh pair, corrupted file) therefore
    collapses to ``stopped``. This matches ``ControlServer._load_stream_state``
    in ``control.py`` so the boot-time default and the runtime default
    cannot drift apart silently.

    Isolated as a module-level helper so the boot-time decision can be
    unit-tested without spinning up the full lifecycle.
    """
    try:
        with open(path) as f:
            value = f.read().strip()
    except OSError:
        return "stopped"
    if value in VALID_STREAM_STATES:
        return value
    return "stopped"


class _ServerResolver:
    """Background retry of ``socket.gethostbyname`` for the configured server.

    The previous one-shot resolution in ``_do_connecting`` (issue #199)
    fired exactly once at boot — if mDNS hadn't published yet (Avahi
    cold-start, multicast rate-limit, slow DHCP), the camera spent its
    early life logging a vague warning while heartbeats failed silently
    until something else triggered a retry. This resolver runs in a
    daemon thread, retries with exponential backoff capped at
    ``MAX_BACKOFF_S``, and surfaces a structured ``mdns_resolution_failed``
    fault on the heartbeat if the deadline expires without success.

    Lifecycle:
      ``start()`` — kick off the daemon thread (idempotent; safe to
        call when no work is pending — addresses without a configured
        server short-circuit).
      ``stop()``  — set the stop event and join the thread. Must be
        called from ``CameraLifecycle.shutdown`` so a fast-shutdown
        path doesn't leak a sleeping retry.

    The resolver does NOT plumb the resolved IP back to anyone — the
    glibc/nss-mdns resolver caches it internally, and the existing
    callers (heartbeat, control channel) re-resolve on use. The
    benefit of running this in the background is purely the early
    fault surfacing + cache priming.
    """

    # Retry tuning. The defaults are calibrated for a typical Yocto
    # boot where Avahi can take 5-20 s to publish; first attempt is
    # quick, then we back off so we don't burn CPU on a permanent fail.
    INITIAL_BACKOFF_S = 2.0
    MAX_BACKOFF_S = 60.0
    BACKOFF_MULTIPLIER = 2.0
    # Total wall-clock cap. After this we emit the fault and stop
    # trying — the user-facing "Server name didn't resolve" badge
    # tells operators to look at the configured address rather than
    # waiting indefinitely.
    DEADLINE_S = 300.0

    def __init__(self, address: str, capture_manager=None):
        self._address = address or ""
        self._capture = capture_manager
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._resolved_ip: str | None = None

    @property
    def resolved_ip(self) -> str | None:
        """The most recent successful resolution, or None if never resolved."""
        return self._resolved_ip

    def start(self) -> None:
        """Launch the resolver thread. No-op if already running or address empty."""
        if not self._address:
            log.debug("ServerResolver: no address configured — skipping")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="server-resolver", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the resolver to stop and join the thread.

        Always interruptible — the inner backoff sleep waits on the
        stop event rather than ``time.sleep``, so shutdown latency is
        bounded by the OS wake-up rather than the current backoff
        interval.
        """
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        deadline = time.monotonic() + self.DEADLINE_S
        backoff = self.INITIAL_BACKOFF_S
        attempts = 0
        while not self._stop.is_set() and time.monotonic() < deadline:
            attempts += 1
            try:
                ip = socket.gethostbyname(self._address)
            except socket.gaierror as e:
                log.debug(
                    "Resolution attempt %d for '%s' failed: %s — retry in %.1fs",
                    attempts,
                    self._address,
                    e,
                    backoff,
                )
                # Wait the backoff interruptibly. Returns True when
                # ``stop()`` was called, in which case we exit cleanly
                # without further retries or fault emission.
                if self._stop.wait(timeout=backoff):
                    return
                backoff = min(backoff * self.BACKOFF_MULTIPLIER, self.MAX_BACKOFF_S)
                continue

            self._resolved_ip = ip
            log.info(
                "Server address resolved after %d attempt(s): %s -> %s",
                attempts,
                self._address,
                ip,
            )
            # If we'd previously emitted the fault (e.g. earlier deadline
            # expired and the network later recovered), clear it so the
            # heartbeat-visible state transition propagates to the
            # dashboard without needing a restart.
            if self._capture is not None:
                try:
                    self._capture.clear_fault(FAULT_NETWORK_MDNS_RESOLUTION_FAILED)
                except AttributeError:
                    # Older CaptureManager stub without the API. Test-only.
                    pass
            return

        if self._stop.is_set():
            return

        # Deadline reached without a successful resolution. Emit the
        # structured fault so the dashboard surfaces a precise badge
        # ("Server name didn't resolve") rather than the camera silently
        # logging warnings while operators wonder why heartbeats are
        # missing.
        log.error(
            "Server address '%s' did not resolve within %.0fs (%d attempts) — "
            "raising %s fault",
            self._address,
            self.DEADLINE_S,
            attempts,
            FAULT_NETWORK_MDNS_RESOLUTION_FAILED,
        )
        if self._capture is not None:
            try:
                self._capture.add_fault(
                    make_fault(
                        FAULT_NETWORK_MDNS_RESOLUTION_FAILED,
                        context={
                            "address": self._address,
                            "attempts": attempts,
                            "deadline_s": self.DEADLINE_S,
                        },
                    )
                )
            except AttributeError:
                pass


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

    def __init__(self, config, platform, shutdown_event, notifier=None):
        self._config = config
        self._platform = platform
        self._is_shutdown = shutdown_event
        self._notifier = notifier
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
        # Background server-name resolver (#199). Replaces the previous
        # one-shot ``gethostbyname`` warning. Started in ``_do_running``
        # once the CaptureManager exists (the resolver injects faults
        # via that), stopped in ``shutdown``.
        self._server_resolver: _ServerResolver | None = None

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

        # Stop the server-name resolver before everything else so a
        # mid-backoff thread doesn't interleave with later teardown
        # logging (the resolver itself owns no other resources, so
        # ordering with the rest is don't-care; we just want a clean
        # join before we report "stopped").
        if self._server_resolver:
            self._server_resolver.stop()
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
        if self._notifier is not None:
            self._notifier.mark_ready()

        while not self._is_shutdown() and self._setup_server.needs_setup():
            if self._notifier is not None:
                self._notifier.beat("setup")
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
        if self._notifier is not None:
            self._notifier.mark_ready()

        # Poll until paired or shutdown
        last_registration_attempt = 0.0
        while not self._is_shutdown():
            if self._notifier is not None:
                self._notifier.beat("pairing")
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

        # The actual resolution moved to a background retry started in
        # ``_do_running`` once the CaptureManager exists (the resolver
        # surfaces a hardware-fault on permanent failure via
        # ``capture_manager.add_fault``). #199 replaces the previous
        # one-shot warning here.

        return True

    def _do_validating(self):
        """Validate camera hardware (V4L2 device)."""
        log.info("--- Camera Hardware Check ---")
        self._capture = CaptureManager(device=self._platform.camera_device)
        if not self._capture.check():
            log.error(
                "Camera device not available. Troubleshooting:\n"
                "  1. Check ribbon cable is seated firmly at both ends\n"
                "  2. Check /boot/config.txt has: gpu_mem=128 and "
                "camera_auto_detect=1 (and no explicit dtoverlay=<sensor>)\n"
                "  3. Run: dmesg | grep -iE 'imx219|ov5647|imx477|imx708' "
                "to confirm which sensor the kernel probed\n"
                "  4. Run: libcamera-hello --list-cameras\n"
                "Supported sensors: OV5647, IMX219, IMX477, IMX708 — auto-detect "
                "selects the right overlay. Will retry via health monitor..."
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

        # Background server-name resolver (#199). Started here rather
        # than in ``_do_connecting`` so it can hand its
        # ``mdns_resolution_failed`` fault to the CaptureManager
        # (created in ``_do_validating`` immediately above us) without
        # threading the dependency through earlier states.
        if self._config.is_configured and self._config.server_ip:
            self._server_resolver = _ServerResolver(
                self._config.server_ip, capture_manager=self._capture
            )
            self._server_resolver.start()

        # RTSP streaming — on-demand per ADR-0017. The camera only starts
        # streaming on boot if both (a) it is configured/paired AND (b) the
        # persisted desired state is "running". A fresh camera defaults to
        # "stopped" and waits for the server to explicitly ask for a stream.
        self._stream = StreamManager(
            self._config,
            camera_device=self._platform.camera_device,
            pairing_manager=self._pairing,
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
            # Capture manager flows into /api/status so the camera's
            # own status page can show a "no camera module detected"
            # banner. Set by _do_validating (the state before us).
            capture_manager=self._capture,
        )
        self._status_server.start()

        # OTA update agent (port 8080)
        self._ota_agent = OTAAgent(self._config)
        self._ota_agent.start()

        vcgencmd_path = getattr(self._platform, "vcgencmd_path", None)
        throttle_path = getattr(self._platform, "throttle_path", None)

        # Heartbeat sender — keeps server informed of liveness (ADR-0016)
        # and reports the persisted desired stream state so the server can
        # detect drift (ADR-0017 §3). The control handler is the single
        # source of truth for desired state.
        self._heartbeat = HeartbeatSender(
            self._config,
            self._pairing,
            stream_manager=self._stream,
            thermal_path=self._platform.thermal_path,
            vcgencmd_path=vcgencmd_path,
            throttle_path=throttle_path,
            control_handler=self._status_server.control_handler,
            # Surface hardware faults ("no camera module detected")
            # to the server so the dashboard can show the user.
            capture_manager=self._capture,
        )
        if self._config.is_configured and self._pairing.is_paired:
            self._heartbeat.start()

        # Health monitoring
        self._health = HealthMonitor(
            self._config,
            self._capture,
            self._stream,
            thermal_path=self._platform.thermal_path,
            vcgencmd_path=vcgencmd_path,
            throttle_path=throttle_path,
            notifier=self._notifier,
        )
        self._health.start()

        led.connected()
        if self._notifier is not None:
            self._notifier.mark_ready()
        log.info("Camera streamer running (camera=%s)", self._config.camera_id)

        # Main loop — wait for shutdown
        while not self._is_shutdown():
            if self._notifier is not None:
                self._notifier.beat("lifecycle")
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
            if self._notifier is not None:
                self._notifier.beat("connecting")

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
        """Legacy one-shot resolver, retained for direct callers/tests.

        Production lifecycle uses ``_ServerResolver`` (started from
        ``_do_running``) which retries with exponential backoff and
        surfaces a hardware-fault on permanent failure (#199). This
        synchronous shim is preserved so older callers/tests that
        invoke ``_resolve_server`` directly continue to work, but it
        is no longer wired into the lifecycle flow.
        """
        addr = self._config.server_ip
        if not addr:
            return
        try:
            ip = socket.gethostbyname(addr)
            log.info("Server address resolved: %s -> %s", addr, ip)
        except socket.gaierror:
            log.warning(
                "Cannot resolve server address '%s' — mDNS may not be ready.",
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
