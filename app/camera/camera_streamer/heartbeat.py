"""
Periodic heartbeat sender — keeps the server informed of camera liveness.

Sends an HMAC-SHA256 signed POST to the server every HEARTBEAT_INTERVAL
seconds. The server uses these to:
  - Update last_seen and mark the camera online
  - Detect stale cameras (no heartbeat → mark offline)
  - Receive live health metrics (CPU temp, memory, streaming state)

If the server responds with a pending_config payload, we apply it
immediately. This closes the retry loop when the server could not push
a config change to the camera while it was unreachable.

Part of the bidirectional control/health protocol (ADR-0016).
"""

import hashlib
import hmac
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

from camera_streamer.control import ControlHandler, parse_control_request

log = logging.getLogger("camera-streamer.heartbeat")

HEARTBEAT_INTERVAL = 15  # seconds between heartbeats
HEARTBEAT_TIMEOUT = 10  # seconds to wait for server response
HEARTBEAT_JITTER = 3  # max random jitter in seconds to spread load

# Unpair detection (ADR-0016 sync protocol)
# When the server deletes a camera we never hear about it directly: the next
# heartbeat just comes back with HTTP 401 "Unknown camera". To converge the
# two sides back to a consistent state we treat N consecutive 401s from a
# reachable server as "the server has forgotten me" and reset to unpaired.
# A threshold (rather than acting on the first 401) avoids flapping during
# transient DB issues or a simultaneous server restart. 5 heartbeats at
# 15s each is ~75s, comfortably above the OFFLINE_TIMEOUT (30s) used on
# the server side.
UNPAIR_401_THRESHOLD = 5


def _build_signature(
    secret_hex: str, camera_id: str, timestamp: str, body_bytes: bytes
) -> str:
    """Compute HMAC-SHA256(secret, camera_id:timestamp:sha256(body))."""
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    message = f"{camera_id}:{timestamp}:{body_hash}"
    return hmac.new(
        bytes.fromhex(secret_hex),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def _ssl_context(certs_dir: str) -> ssl.SSLContext:
    """Build SSL context with the camera's mTLS client certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # server uses self-signed cert

    cert = os.path.join(certs_dir, "client.crt")
    key = os.path.join(certs_dir, "client.key")
    if os.path.isfile(cert) and os.path.isfile(key):
        ctx.load_cert_chain(cert, key)

    return ctx


def _get_uptime_seconds() -> int:
    """Return system uptime in seconds from /proc/uptime."""
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _get_memory_percent() -> int:
    """Return approximate memory usage percentage from /proc/meminfo."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", info.get("MemFree", 0))
        if total > 0:
            return int((total - available) / total * 100)
    except (OSError, ValueError, KeyError):
        pass
    return 0


def _get_firmware_version() -> str:
    """Read the first version string from ``/etc/sw-versions``.

    The file is written by SWUpdate post-install and lists
    ``<component> <version>`` pairs. Callers tolerate an empty return
    (missing file, unreadable, bad format) by reporting ``""``.
    """
    try:
        with open("/etc/sw-versions") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2:
                    return parts[1]
    except OSError:
        pass
    return ""


def _get_cpu_temp(thermal_path: str | None) -> float:
    """Return CPU temperature in °C."""
    paths = []
    if thermal_path:
        paths.append(thermal_path)
    paths.append("/sys/class/thermal/thermal_zone0/temp")
    for path in paths:
        try:
            with open(path) as f:
                return round(int(f.read().strip()) / 1000, 1)
        except (OSError, ValueError):
            continue
    return 0.0


class HeartbeatSender:
    """Sends periodic heartbeats to the paired server.

    Args:
        config: ConfigManager instance.
        pairing_manager: PairingManager instance.
        stream_manager: StreamManager instance (may be None).
        thermal_path: Optional path to CPU thermal zone file.
    """

    def __init__(
        self,
        config,
        pairing_manager,
        stream_manager=None,
        thermal_path=None,
        control_handler=None,
    ):
        self._config = config
        self._pairing = pairing_manager
        self._stream = stream_manager
        self._thermal_path = thermal_path
        # Optional ControlHandler — when provided, the heartbeat reports the
        # *persisted desired* state (ADR-0017), which is what the server
        # compares against to detect drift. Tests that don't care about
        # drift pass None and we fall back to the live streaming flag.
        self._control = control_handler
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        # Track consecutive 401 Unknown-camera responses to detect server-side unpair
        self._consecutive_unknown_camera = 0

    def start(self) -> None:
        """Start the heartbeat background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="heartbeat",
            daemon=True,
        )
        self._thread.start()
        log.info("Heartbeat sender started (interval=%ds)", HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        """Stop the heartbeat thread (blocks until it exits)."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=HEARTBEAT_TIMEOUT + 2)
            self._thread = None
        log.info("Heartbeat sender stopped")

    def send_once(self) -> dict | None:
        """Send a single heartbeat immediately. Returns server response or None."""
        return self._send()

    # ---- Internal ----

    def _run(self) -> None:
        """Background thread: send heartbeat every HEARTBEAT_INTERVAL seconds."""
        # Jitter the first heartbeat so cameras don't all fire at the same second
        import random

        time.sleep(random.uniform(0, HEARTBEAT_JITTER))

        while not self._stop_event.is_set():
            try:
                response = self._send()
                if response and response.get("pending_config"):
                    self._apply_pending_config(response["pending_config"])
            except Exception as exc:
                log.warning("Heartbeat error: %s", exc)

            self._stop_event.wait(timeout=HEARTBEAT_INTERVAL)

    def _build_payload(self) -> dict:
        """Assemble the heartbeat payload from live system state."""
        streaming = bool(self._stream and self._stream.is_streaming)
        config = self._config

        # ADR-0017: report desired state when a control handler is wired
        # (production path), falling back to the live streaming flag for
        # tests that construct HeartbeatSender without a handler.
        if self._control is not None:
            stream_state = self._control.desired_stream_state
        else:
            stream_state = "running" if streaming else "stopped"

        return {
            "camera_id": config.camera_id,
            "timestamp": int(time.time()),
            "streaming": streaming,
            "stream_state": stream_state,
            "cpu_temp": _get_cpu_temp(self._thermal_path),
            "memory_percent": _get_memory_percent(),
            "uptime_seconds": _get_uptime_seconds(),
            "firmware_version": _get_firmware_version(),
            "stream_config": {
                "width": config.width,
                "height": config.height,
                "fps": config.fps,
                "bitrate": config.bitrate,
                "h264_profile": config.h264_profile,
                "keyframe_interval": config.keyframe_interval,
                "rotation": config.rotation,
                "hflip": config.hflip,
                "vflip": config.vflip,
            },
        }

    def _send(self) -> dict | None:
        """POST one heartbeat to the server. Returns parsed response or None."""
        server_ip = self._config.server_ip
        if not server_ip:
            log.debug("No server IP configured — skipping heartbeat")
            return None

        secret = self._pairing.get_pairing_secret()
        if not secret:
            log.debug("Not paired — skipping heartbeat")
            return None

        camera_id = self._config.camera_id
        payload = self._build_payload()
        body = json.dumps(payload).encode()
        timestamp = str(payload["timestamp"])
        signature = _build_signature(secret, camera_id, timestamp, body)

        url = f"https://{server_ip}/api/v1/cameras/heartbeat"
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Camera-ID": camera_id,
                "X-Timestamp": timestamp,
                "X-Signature": signature,
            },
        )

        try:
            ctx = _ssl_context(self._config.certs_dir)
            with urllib.request.urlopen(
                req, context=ctx, timeout=HEARTBEAT_TIMEOUT
            ) as resp:
                resp_body = resp.read()
                result = json.loads(resp_body) if resp_body else {}
                log.debug("Heartbeat accepted by server (HTTP %d)", resp.status)
                # Server accepted us — reset the unpair-detection counter
                self._consecutive_unknown_camera = 0
                return result
        except urllib.error.HTTPError as e:
            # 401 + "Unknown camera" means the server no longer has this camera
            # in its database (admin deleted it). After UNPAIR_401_THRESHOLD
            # consecutive such responses we assume the server really did unpair
            # us and reset local state so the camera goes back into PAIRING.
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code == 401 and "Unknown camera" in body_text:
                self._consecutive_unknown_camera += 1
                log.warning(
                    "Heartbeat rejected by server: HTTP 401 Unknown camera "
                    "(%d/%d) — server has forgotten this camera",
                    self._consecutive_unknown_camera,
                    UNPAIR_401_THRESHOLD,
                )
                if self._consecutive_unknown_camera >= UNPAIR_401_THRESHOLD:
                    self._handle_server_unpair()
            else:
                log.warning("Heartbeat rejected by server: HTTP %d", e.code)
        except (urllib.error.URLError, OSError) as e:
            # Network errors don't count as "server unpaired me" — the server
            # might just be offline or the network might be flaky.
            log.debug("Heartbeat failed (server %s): %s", server_ip, e)
        return None

    def _handle_server_unpair(self) -> None:
        """Wipe local pairing state and exit so systemd restarts us into PAIRING.

        Called when the server has repeatedly rejected heartbeats with
        ``401 Unknown camera``. We delete client.crt/key and pairing_secret
        so ``PairingManager.is_paired`` flips to False, then signal the
        process to exit. systemd restarts camera-streamer and
        ``CameraLifecycle._do_pairing`` runs again — the camera registers
        itself as pending on the server and opens the /pair page.
        """
        log.error(
            "Server has unpaired this camera (%d consecutive 401s). "
            "Wiping local pairing state and restarting to re-enter pairing mode.",
            self._consecutive_unknown_camera,
        )
        certs_dir = self._config.certs_dir
        for name in ("client.crt", "client.key", "pairing_secret"):
            path = os.path.join(certs_dir, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    log.info("Removed %s", path)
            except OSError as exc:
                log.warning("Failed to remove %s: %s", path, exc)

        # Stop heartbeat loop — no more attempts from this thread.
        self._stop_event.set()
        # Signal the main process to exit so systemd restarts us cleanly.
        # os.kill(pid, SIGTERM) is reliable regardless of how the service was
        # launched (systemd, Docker, manual run). The main.py signal handler
        # sets _shutdown=True, the lifecycle tears down gracefully, and systemd
        # sees the exit and starts a fresh process in PAIRING state.
        # (Calling "systemctl restart" from within the service is unreliable:
        # on some systemd versions it blocks waiting for the unit to stop,
        # creating a deadlock, and on BusyBox-based Yocto images the systemctl
        # binary may silently do nothing.)
        import signal as _signal

        try:
            os.kill(os.getpid(), _signal.SIGTERM)
        except OSError as exc:
            log.warning("Failed to send SIGTERM to self: %s", exc)

    def _apply_pending_config(self, pending: dict) -> None:
        """Apply a pending stream config pushed back by the server."""
        log.info("Applying pending config from server heartbeat response: %s", pending)
        try:
            body = json.dumps(pending).encode()
            params, _, err = parse_control_request(body)
            if err:
                log.warning("Invalid pending config from server: %s", err)
                return
            handler = ControlHandler(self._config, stream_manager=self._stream)
            result, error, _ = handler.set_config(params, request_id=0, origin="server")
            if error:
                log.warning("Failed to apply pending config: %s", error)
            else:
                log.info("Pending config applied: %s", result)
        except Exception as exc:
            log.warning("Exception applying pending config: %s", exc)
