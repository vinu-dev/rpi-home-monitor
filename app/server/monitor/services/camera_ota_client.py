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

log = logging.getLogger("monitor.camera_ota_client")

OTA_PORT = 8080
UPLOAD_PATH = "/ota/upload"
STATUS_PATH = "/ota/status"

# Chunk size for streaming the bundle to the camera. 256 KiB keeps
# TLS record framing efficient while bounding server-side RAM use.
CHUNK_SIZE = 256 * 1024

# Overall upload timeout: a 150 MB bundle over 2.4 GHz WiFi at ~4 MB/s
# takes ~40 s. Add headroom for slow links. Status polling uses its own
# short timeout.
UPLOAD_TIMEOUT = 600  # 10 minutes
STATUS_TIMEOUT = 10


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

    def push_bundle(self, camera_ip, bundle_path, progress_cb=None):
        """Stream a .swu bundle to a camera's OTA agent.

        Args:
            camera_ip: Camera IP address.
            bundle_path: Absolute path to .swu file on server disk.
            progress_cb: Optional callback(bytes_sent, total_bytes)
                invoked after every chunk. Must be fast and thread-safe.

        Returns:
            (ok: bool, message_or_error: str). On success the message
            is the camera's response body (JSON-decoded to a string).
            On failure the error string is human-readable.

        The push is synchronous — the caller runs this on a background
        thread and polls get_status() from the UI.
        """
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

            if 200 <= resp.status < 300:
                msg = payload.get("message") if isinstance(payload, dict) else ""
                log.info(
                    "OTA push to %s ok (%d bytes, status=%d, msg=%r)",
                    camera_ip,
                    sent,
                    resp.status,
                    msg,
                )
                return True, msg or "Installed"

            err = ""
            if isinstance(payload, dict):
                err = payload.get("error") or payload.get("raw") or ""
            err = err or f"HTTP {resp.status}"
            log.warning("OTA push to %s failed: %s", camera_ip, err)
            return False, err

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
