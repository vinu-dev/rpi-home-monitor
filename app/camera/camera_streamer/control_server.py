# REQ: SWR-013, SWR-039; RISK: RISK-002, RISK-007; SEC: SC-001, SC-002; TEST: TC-004, TC-037
"""
Dedicated HTTPS listener for the camera's server-only control API.

Issue #113 splits machine control off the human-admin listener so the
control plane can require mTLS at the TLS layer without affecting
browser compatibility on port 443.
"""

import http.server
import json
import logging
import os
import ssl
import threading

from camera_streamer import status_server as status_server_module
from camera_streamer.control import parse_control_request

log = logging.getLogger("camera-streamer.control-server")

CONTROL_LISTEN_PORT = 8443
CONTROL_API_PREFIX = "/api/v1/control/"
CONTROL_ROUTE_MATRIX = {
    "GET": frozenset(
        {
            "/api/v1/control/config",
            "/api/v1/control/capabilities",
            "/api/v1/control/status",
            "/api/v1/control/stream/state",
        }
    ),
    "PUT": frozenset({"/api/v1/control/config"}),
    "POST": frozenset(
        {
            "/api/v1/control/restart-stream",
            "/api/v1/control/stream/start",
            "/api/v1/control/stream/stop",
        }
    ),
}


def _control_ca_path(config):
    """Return the CA bundle used to verify the paired server certificate."""
    return os.path.join(config.certs_dir, "ca.crt")


def _wrap_https_server(server, config):
    """Wrap the control server socket with mTLS-required TLS."""
    cert_path, key_path = status_server_module._ensure_tls_material(config)
    ca_path = _control_ca_path(config)
    if not os.path.isfile(ca_path):
        raise FileNotFoundError(f"missing control-listener CA bundle: {ca_path}")

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    ctx.check_hostname = False
    ctx.load_verify_locations(ca_path)
    ctx.verify_mode = ssl.CERT_REQUIRED
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server


class CameraControlServer:
    """Dedicated HTTPS listener for `/api/v1/control/*` routes."""

    def __init__(self, config, control_handler, stream_manager=None):
        if control_handler is None:
            raise ValueError("control_handler is required")
        self._config = config
        self._control = control_handler
        self._stream = stream_manager
        self._server = None
        self._thread = None

    def start(self):
        """Start the mTLS-only control listener when pairing material exists."""
        ca_path = _control_ca_path(self._config)
        if not os.path.isfile(ca_path):
            log.info("Control listener disabled until pairing (missing %s)", ca_path)
            return False

        handler = _make_control_handler(self._control, self._stream)
        server = None
        try:
            server = http.server.HTTPServer(("0.0.0.0", CONTROL_LISTEN_PORT), handler)
            self._server = _wrap_https_server(server, self._config)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="control-https",
            )
            self._thread.start()
            log.info("Control server listening on HTTPS port %d", CONTROL_LISTEN_PORT)
            return True
        except Exception as exc:
            if server is not None:
                try:
                    server.server_close()
                except OSError:
                    pass
            self._server = None
            self._thread = None
            log.error("Failed to start control server: %s", exc)
            return False

    def stop(self):
        """Stop the control HTTPS server."""
        if self._server:
            if self._thread and self._thread.is_alive():
                self._server.shutdown()
                self._thread.join(timeout=5)
            self._server.server_close()
            self._server = None
            self._thread = None
            log.info("Control server stopped")


def _make_control_handler(control_handler, stream_manager):
    """Create the HTTP adapter for the mTLS-only control API."""

    class ControlRequestHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            log.debug("Control HTTPS: " + format % args)

        def _has_mtls_client_cert(self):
            """Return True when the peer presented a validated client cert."""
            try:
                peer_cert = self.request.getpeercert()
            except (AttributeError, ValueError):
                return False
            return bool(peer_cert)

        def _require_mtls(self):
            """Keep an application-layer guard in addition to CERT_REQUIRED."""
            if self._has_mtls_client_cert():
                return True
            self._json_response({"error": "Client certificate required"}, 401)
            return False

        def _json_response(self, data, code=200):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._require_mtls():
                return

            if self.path == "/api/v1/control/config":
                self._json_response(control_handler.get_config())
                return
            if self.path == "/api/v1/control/capabilities":
                self._json_response(control_handler.get_capabilities())
                return
            if self.path == "/api/v1/control/status":
                self._json_response(control_handler.get_status())
                return
            if self.path == "/api/v1/control/stream/state":
                self._json_response(control_handler.get_stream_state())
                return

            self.send_error(404)

        def do_PUT(self):
            if not self._require_mtls():
                return

            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b""

            if self.path == "/api/v1/control/config":
                params, request_id, err = parse_control_request(body)
                if err:
                    self._json_response({"error": err}, 400)
                    return
                result, error, status = control_handler.set_config(params, request_id)
                if error:
                    self._json_response({"error": error}, status)
                else:
                    self._json_response(result, status)
                return

            self.send_error(404)

        def do_POST(self):
            if not self._require_mtls():
                return

            if self.path == "/api/v1/control/restart-stream":
                if stream_manager:
                    ok = stream_manager.restart()
                    self._json_response({"restarted": ok, "status": "ok"})
                else:
                    self._json_response({"error": "Stream manager not available"}, 503)
                return
            if self.path == "/api/v1/control/stream/start":
                result, error, status = control_handler.set_stream_state("running")
                if error:
                    self._json_response({"error": error}, status)
                else:
                    self._json_response(result, status)
                return
            if self.path == "/api/v1/control/stream/stop":
                result, error, status = control_handler.set_stream_state("stopped")
                if error:
                    self._json_response({"error": error}, status)
                else:
                    self._json_response(result, status)
                return

            self.send_error(404)

    return ControlRequestHandler
