"""
OTA update agent — server→camera push endpoint (ADR-0008, ADR-0020).

Accepts mTLS-authenticated .swu uploads from the home server on :8080
and hands them to the privileged installer via the trigger-file
protocol in `ota_installer` (camera-streamer cannot run swupdate -i
directly — it is unprivileged and NoNewPrivileges=true).

Flow:
    POST /ota/upload
      1. Stream body to /var/lib/camera-ota/staging/update.swu.partial
      2. Atomic rename to update.swu on complete
      3. Write trigger → systemd .path fires camera-ota-installer.service
         (root, runs `swupdate -c -i` then `swupdate -i`)
      4. Return HTTP 202 Accepted immediately — DO NOT block on install.
         The server polls GET /ota/status until terminal state.

    GET /ota/status
      Returns the current status.json content.

Why we don't block on install here:
    The Pi Zero 2W has only 362 MB RAM. Holding an mTLS HTTPS
    connection open for ~3 min while the root installer writes
    1.8 GB of ext4 to the SD card triggers the OOM killer — we've
    observed camera-streamer, sshd, and getty all killed mid-install,
    leaving the box unreachable until a physical power cycle. The
    camera-direct GUI upload path on :443 already uses the
    fire-and-poll pattern; OTAAgent matches it so the two transports
    converge on the same memory profile.

mTLS hardening: if client certs are present the socket enforces
CERT_REQUIRED. Absence of certs (pre-pairing dev flows) falls back
to plain HTTP — the firewall drops :8080 from non-LAN anyway.

Design patterns:
- Constructor Injection (config)
- Delegation (install logic lives in ota_installer)
- Fail-Silent (agent errors don't crash camera main loop)
"""

import json
import logging
import os
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from camera_streamer import ota_installer

log = logging.getLogger("camera-streamer.ota-agent")

OTA_PORT = 8080
MAX_BUNDLE_SIZE = 500 * 1024 * 1024  # 500MB


class OTAAgent:
    """Camera-side OTA update agent (server→camera push transport).

    Args:
        config: ConfigManager instance (used for cert paths).
    """

    def __init__(self, config):
        self._config = config
        self._server = None
        self._thread = None
        self._running = False

    @property
    def status(self):
        """Return current OTA status (proxied from installer status.json)."""
        return ota_installer.read_status()

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
        body = json.dumps(self.status).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _handle_upload(self, handler):
        content_length = int(handler.headers.get("Content-Length", 0))
        if content_length <= 0:
            self._send_json(handler, 400, {"error": "No content"})
            return
        if content_length > MAX_BUNDLE_SIZE:
            self._send_json(handler, 400, {"error": "Bundle too large"})
            return
        if ota_installer.is_busy():
            self._send_json(handler, 409, {"error": "Install already in progress"})
            return

        ok, msg = ota_installer.stage_bundle(handler.rfile, content_length)
        if not ok:
            self._send_json(handler, 500, {"error": msg})
            return

        ok, trigger_msg = ota_installer.trigger_install(msg)
        if not ok:
            self._send_json(handler, 500, {"error": trigger_msg})
            return

        # Return 202 Accepted immediately. The server polls GET /ota/status
        # for progress; blocking here until swupdate finishes kept the
        # mTLS connection open for several minutes and pushed a Pi Zero
        # 2W into OOM-kill territory (ADR notes).
        self._send_json(
            handler,
            202,
            {"message": "Install triggered", "bundle_bytes": content_length},
        )
        log.info("OTA upload accepted (%d bytes) — installer triggered", content_length)

    def _send_json(self, handler, status_code, data):
        body = json.dumps(data).encode()
        handler.send_response(status_code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
