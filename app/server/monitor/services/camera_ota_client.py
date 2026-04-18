"""
Camera OTA client — streams .swu bundles to a camera's OTA agent.

The camera runs an OTAAgent on port 8080 (camera_streamer/ota_agent.py)
protected by mTLS using the pairing CA/cert. This client uses the
server's mTLS credentials to authenticate and streams the bundle
directly from disk without buffering in RAM (bundles are ~150 MB).

Transport: HTTPS POST to https://<camera_ip>:8080/ota/upload with
the raw .swu body and a Content-Length header. The camera streams
the upload straight to disk, verifies the CMS signature via
`swupdate -c`, and installs via `swupdate -i`.

Status: the agent exposes GET /ota/status returning
{state: idle|downloading|verifying|installing|installed|error,
 progress: 0..100, error: ""}. Callers poll it while the push runs.

Design patterns:
- Constructor Injection (certs_dir)
- Stream-to-Wire (never load full bundle into memory)
- Fail-Graceful (returns error, does not raise)
"""

import http.client
import json
import logging
import os
import ssl
import time

log = logging.getLogger("monitor.camera_ota_client")

OTA_PORT = 8080
UPLOAD_PATH = "/ota/upload"
STATUS_PATH = "/ota/status"

# Chunk size for streaming the bundle to the camera. 256 KiB keeps
# TLS record framing efficient while bounding server-side RAM use.
CHUNK_SIZE = 256 * 1024

# The upload POST only holds the connection for the bytes-in-flight
# phase — the camera acks with 202 after it writes the trigger, then
# the install runs async. 300 s covers a slow-link transfer; no
# need to wait for the (much longer) install here.
UPLOAD_TIMEOUT = 300
STATUS_TIMEOUT = 10

# Poll timings while the root installer runs. The full install on a
# Pi Zero 2W can take ~3 min — we cap at 15 min with a 5 s poll.
INSTALL_POLL_INTERVAL = 5
INSTALL_POLL_TIMEOUT = 900


class CameraOTAClient:
    """Push .swu bundles to a camera's OTA agent over mTLS.

    Args:
        certs_dir: Path to server certificate directory (server.crt,
            server.key, ca.crt — same material used by
            CameraControlClient for the control channel).
    """

    def __init__(self, certs_dir):
        self._certs_dir = certs_dir

    def _ssl_context(self):
        """Build mTLS client context.

        The camera's OTAAgent requires CERT_REQUIRED with its pairing
        CA, so we must present a valid server cert. Hostname checking
        is off because the camera's cert is self-signed against its
        local hostname.
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # camera presents self-signed cert

        cert_path = os.path.join(self._certs_dir, "server.crt")
        key_path = os.path.join(self._certs_dir, "server.key")
        if not (os.path.isfile(cert_path) and os.path.isfile(key_path)):
            raise FileNotFoundError(
                f"Server mTLS material not found in {self._certs_dir}"
            )
        ctx.load_cert_chain(cert_path, key_path)
        return ctx

    def push_bundle(self, camera_ip, bundle_path, progress_cb=None, status_cb=None):
        """Stream a .swu bundle to a camera's OTA agent.

        Args:
            camera_ip: Camera IP address.
            bundle_path: Absolute path to .swu file on server disk.
            progress_cb: Optional callback(bytes_sent, total_bytes)
                invoked during the upload phase for byte-level progress.
                Must be fast and thread-safe.
            status_cb: Optional callback(state, progress, error="") that
                reports high-level phase transitions — state ∈
                {"uploading", "installing", "rebooting", "installed",
                "error"} and progress in 0..100. Enables the UI to show
                "rebooting — waiting for camera" during the window when
                the camera has dropped off the network mid-install and
                before it comes back to confirm.

        Returns:
            (ok: bool, message_or_error: str). On success the message
            is the camera's response body (JSON-decoded to a string).
            On failure the error string is human-readable.

        The push is synchronous — the caller runs this on a background
        thread and polls get_status() from the UI.
        """

        def _emit(state, progress, error=""):
            if status_cb is None:
                return
            try:
                status_cb(state, progress, error)
            except Exception as cb_exc:
                log.debug("status_cb raised: %s", cb_exc)

        if not os.path.isfile(bundle_path):
            return False, f"Bundle not found: {bundle_path}"

        try:
            total = os.path.getsize(bundle_path)
        except OSError as exc:
            return False, f"Cannot stat bundle: {exc}"
        if total <= 0:
            return False, "Bundle is empty"

        try:
            ctx = self._ssl_context()
        except (FileNotFoundError, ssl.SSLError, OSError) as exc:
            return False, f"TLS setup failed: {exc}"

        conn = None
        try:
            conn = http.client.HTTPSConnection(
                camera_ip, OTA_PORT, context=ctx, timeout=UPLOAD_TIMEOUT
            )
            conn.putrequest("POST", UPLOAD_PATH)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(total))
            conn.endheaders()

            sent = 0
            with open(bundle_path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    conn.send(chunk)
                    sent += len(chunk)
                    if progress_cb is not None:
                        try:
                            progress_cb(sent, total)
                        except Exception as cb_exc:
                            log.debug("progress_cb raised: %s", cb_exc)

            resp = conn.getresponse()
            body = resp.read()
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {"raw": body.decode("utf-8", errors="replace")}

            if not (200 <= resp.status < 300):
                err = ""
                if isinstance(payload, dict):
                    err = payload.get("error") or payload.get("raw") or ""
                err = err or f"HTTP {resp.status}"
                log.warning("OTA push to %s upload failed: %s", camera_ip, err)
                return False, err

            log.info(
                "OTA push to %s uploaded (%d bytes, status=%d) — polling install",
                camera_ip,
                sent,
                resp.status,
            )

        except ssl.SSLError as exc:
            log.warning("OTA push to %s TLS error: %s", camera_ip, exc)
            return False, f"TLS error: {exc}"
        except OSError as exc:
            log.warning("OTA push to %s I/O error: %s", camera_ip, exc)
            return False, f"I/O error: {exc}"
        except http.client.HTTPException as exc:
            log.warning("OTA push to %s HTTP error: %s", camera_ip, exc)
            return False, f"HTTP error: {exc}"
        finally:
            if conn is not None:
                conn.close()

        # Poll the camera's status endpoint until the root installer
        # reaches a terminal state. The upload POST above returned 202
        # as soon as the trigger file was written; the actual install
        # runs async on the camera to keep RAM pressure low on the
        # Pi Zero 2W.
        #
        # State transitions surfaced to the UI (via status_cb):
        #   - "installing" while the camera is reachable and reports
        #     progress through its /ota/status endpoint.
        #   - "rebooting" once we've seen the camera drop off AFTER we
        #     confirmed it was installing — this is the expected window
        #     when it cycles into the new slot and OTAAgent isn't
        #     listening yet.
        #   - "installed" when the camera comes back and its
        #     status.json is "installed".
        #   - "error" on explicit camera-reported failure.
        _emit("installing", 50)
        deadline = time.time() + INSTALL_POLL_TIMEOUT
        last_progress = -1
        saw_reachable = False
        announced_rebooting = False
        while time.time() < deadline:
            status, err = self.get_status(camera_ip)
            if status is None:
                if saw_reachable and not announced_rebooting:
                    # Camera was alive a moment ago and is gone now —
                    # that's the expected reboot window.
                    log.info("OTA push to %s: camera rebooting", camera_ip)
                    _emit("rebooting", 90)
                    announced_rebooting = True
                log.debug("status poll transient error for %s: %s", camera_ip, err)
                time.sleep(INSTALL_POLL_INTERVAL)
                continue

            saw_reachable = True
            state = status.get("state", "")
            progress = status.get("progress", 0)
            if progress != last_progress:
                if progress_cb is not None:
                    try:
                        progress_cb(total + progress, total * 2)
                    except Exception as cb_exc:
                        log.debug("progress_cb raised: %s", cb_exc)
                last_progress = progress
            # If camera reports installing/verifying, surface it — this
            # overrides any earlier "rebooting" we might have announced
            # if the camera came back up mid-install (rare, but possible
            # on flaky WiFi).
            if state in ("installing", "verifying", "downloading"):
                # Map the camera's 0..100 install progress into 50..95
                # of the overall push — leave 95..100 for the reboot /
                # post-boot confirmation windows.
                overall = 50 + int(progress * 45 / 100)
                _emit("installing", overall)
                announced_rebooting = False
            if state == "installed":
                log.info("OTA push to %s installed (polled)", camera_ip)
                _emit("installed", 100)
                return True, "Installed"
            if state == "error":
                err = status.get("error") or "install failed"
                log.warning("OTA push to %s install error: %s", camera_ip, err)
                _emit("error", last_progress if last_progress >= 0 else 50, err)
                return False, err
            time.sleep(INSTALL_POLL_INTERVAL)

        return False, "Install timed out waiting for camera to finish"

    def get_status(self, camera_ip):
        """Fetch the camera OTA agent's current status.

        Returns:
            (status_dict, error_string). On success error is "".
            status_dict has {state, progress, error}.
        """
        try:
            ctx = self._ssl_context()
        except (FileNotFoundError, ssl.SSLError, OSError) as exc:
            return None, f"TLS setup failed: {exc}"

        conn = None
        try:
            conn = http.client.HTTPSConnection(
                camera_ip, OTA_PORT, context=ctx, timeout=STATUS_TIMEOUT
            )
            conn.request("GET", STATUS_PATH)
            resp = conn.getresponse()
            body = resp.read()
            if not (200 <= resp.status < 300):
                return None, f"HTTP {resp.status}"
            try:
                return json.loads(body), ""
            except json.JSONDecodeError as exc:
                return None, f"Invalid JSON from camera: {exc}"
        except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
            return None, f"Camera unreachable: {exc}"
        finally:
            if conn is not None:
                conn.close()
