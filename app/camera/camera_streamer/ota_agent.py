"""
OTA update agent (ADR-0008).

Listens for update pushes from the home server over mTLS.
When an update is received:
1. Stream .swu bundle to disk (never buffer in RAM — 512MB camera)
2. Verify Ed25519 signature via swupdate -c
3. Install via swupdate -i (A/B partition swap)
4. Report status back to server
5. If boot fails 3 times → automatic rollback (U-Boot bootlimit)

The agent runs a small HTTP server on port 8080.
When paired (certs available), it uses mTLS. Otherwise plain HTTP.

Design patterns:
- Constructor Injection (config)
- Stream-to-Disk (never buffer full bundle in RAM)
- Fail-Silent (agent errors don't crash camera main loop)
"""

import logging
import os
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger("camera-streamer.ota-agent")

OTA_PORT = 8080
MAX_BUNDLE_SIZE = 500 * 1024 * 1024  # 500MB
CHUNK_SIZE = 64 * 1024  # 64KB chunks for streaming to disk


class OTAAgent:
    """Camera-side OTA update agent.

    Runs an HTTP server that accepts .swu bundle uploads from the
    home server, verifies them, and installs via swupdate.

    Args:
        config: ConfigManager instance for paths and settings.
    """

    def __init__(self, config):
        self._config = config
        self._server = None
        self._thread = None
        self._running = False
        self._status = {"state": "idle", "progress": 0, "error": ""}
        self._status_lock = threading.Lock()

    @property
    def status(self):
        """Return current OTA status."""
        with self._status_lock:
            return dict(self._status)

    @property
    def staging_dir(self):
        return os.path.join(self._config.data_dir, "ota", "staging")

    def _set_status(self, state, **kwargs):
        """Update OTA status (thread-safe)."""
        with self._status_lock:
            self._status["state"] = state
            self._status.update(kwargs)

    def start(self):
        """Start the OTA HTTP server in a background thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._run_server, daemon=True, name="ota-agent"
        )
        self._thread.start()
        log.info("OTA agent started on port %d", OTA_PORT)

    def stop(self):
        """Stop the OTA HTTP server."""
        self._running = False
        if self._server:
            self._server.shutdown()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run_server(self):
        """Run the HTTP server (blocking)."""
        agent = self

        class OTAHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == "/ota/upload":
                    agent._handle_upload(self)
                else:
                    self.send_error(404)

            def do_GET(self):
                if self.path == "/ota/status":
                    agent._handle_status(self)
                else:
                    self.send_error(404)

            def log_message(self, format, *args):
                log.debug("OTA HTTP: %s", format % args)

        try:
            self._server = HTTPServer(("0.0.0.0", OTA_PORT), OTAHandler)
            self._server = self._wrap_tls(self._server)
            while self._running:
                self._server.handle_request()
        except Exception:
            log.exception("OTA agent server error")

    def _wrap_tls(self, server):
        """Wrap server socket with mTLS if certs are available."""
        certs_dir = self._config.certs_dir
        cert_path = os.path.join(certs_dir, "client.crt")
        key_path = os.path.join(certs_dir, "client.key")
        ca_path = os.path.join(certs_dir, "ca.crt")

        if not all(os.path.isfile(p) for p in [cert_path, key_path, ca_path]):
            log.warning("mTLS certs not found — OTA agent running without TLS")
            return server

        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert_path, key_path)
            ctx.load_verify_locations(ca_path)
            ctx.verify_mode = ssl.CERT_REQUIRED
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
            log.info("OTA agent using mTLS")
        except (ssl.SSLError, OSError) as e:
            log.warning("Failed to enable mTLS for OTA agent: %s", e)

        return server

    def _handle_status(self, handler):
        """Handle GET /ota/status — return current update status."""
        import json

        body = json.dumps(self.status).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _handle_upload(self, handler):
        """Handle POST /ota/upload — receive and install .swu bundle.

        Streams the upload directly to disk to avoid OOM on the
        512MB camera. Never buffers the full bundle in memory.
        """
        content_length = int(handler.headers.get("Content-Length", 0))
        if content_length <= 0:
            self._send_json(handler, 400, {"error": "No content"})
            return

        if content_length > MAX_BUNDLE_SIZE:
            self._send_json(handler, 400, {"error": "Bundle too large"})
            return

        self._set_status("downloading", progress=0, error="")

        # Stream to disk
        os.makedirs(self.staging_dir, exist_ok=True)
        bundle_path = os.path.join(self.staging_dir, "update.swu")

        try:
            received = 0
            with open(bundle_path, "wb") as f:
                while received < content_length:
                    chunk_size = min(CHUNK_SIZE, content_length - received)
                    chunk = handler.rfile.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    progress = int((received / content_length) * 50)
                    self._set_status("downloading", progress=progress)

            if received != content_length:
                self._set_status("error", error="Incomplete upload")
                self._send_json(handler, 400, {"error": "Incomplete upload"})
                return

        except OSError as e:
            self._set_status("error", error=str(e))
            self._send_json(handler, 500, {"error": f"Write failed: {e}"})
            return

        log.info("OTA bundle received: %d bytes", received)

        # Verify
        self._set_status("verifying", progress=50)
        valid, verify_err = self._verify_bundle(bundle_path)
        if not valid:
            self._set_status("error", error=verify_err)
            self._cleanup(bundle_path)
            self._send_json(handler, 400, {"error": verify_err})
            return

        # Install
        self._set_status("installing", progress=60)
        ok, install_err = self._install_bundle(bundle_path)
        if not ok:
            self._set_status("error", error=install_err)
            self._cleanup(bundle_path)
            self._send_json(handler, 500, {"error": install_err})
            return

        self._set_status("installed", progress=100, error="")
        self._cleanup(bundle_path)
        log.info("OTA installation complete — reboot required")
        self._send_json(handler, 200, {"message": "Installed — reboot required"})

    def _verify_bundle(self, bundle_path):
        """Verify Ed25519 signature of a .swu bundle.

        Returns:
            (valid, error) tuple.
        """
        public_key = os.path.join(self._config.certs_dir, "swupdate-public.pem")
        if not os.path.isfile(public_key):
            log.warning("No public key found — skipping verification (dev mode)")
            return True, ""

        try:
            result = subprocess.run(
                ["swupdate", "-c", "-i", bundle_path, "-k", public_key],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                log.info("Bundle signature verified")
                return True, ""
            error = result.stderr.strip() or "Signature verification failed"
            log.error("Bundle verification failed: %s", error)
            return False, error

        except FileNotFoundError:
            log.warning("swupdate not found — skipping verification (dev mode)")
            return True, ""
        except subprocess.TimeoutExpired:
            return False, "Verification timed out"
        except OSError as e:
            return False, str(e)

    def _install_bundle(self, bundle_path):
        """Install a .swu bundle via swupdate.

        Returns:
            (success, error) tuple.
        """
        try:
            result = subprocess.run(
                ["swupdate", "-i", bundle_path],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode == 0:
                log.info("swupdate installation complete")
                return True, ""
            error = result.stderr.strip() or "Installation failed"
            log.error("swupdate installation failed: %s", error)
            return False, error

        except FileNotFoundError:
            return False, "swupdate not installed"
        except subprocess.TimeoutExpired:
            return False, "Installation timed out (10 min)"
        except OSError as e:
            return False, str(e)

    def _cleanup(self, bundle_path):
        """Remove bundle file after install or failure."""
        try:
            if os.path.isfile(bundle_path):
                os.remove(bundle_path)
        except OSError:
            pass

    def _send_json(self, handler, status_code, data):
        """Send a JSON response."""
        import json

        body = json.dumps(data).encode()
        handler.send_response(status_code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
