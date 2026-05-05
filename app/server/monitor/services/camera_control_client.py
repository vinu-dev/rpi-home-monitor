# REQ: SWR-039; RISK: RISK-002, RISK-007, RISK-015; SEC: SC-002; TEST: TC-037
"""
Camera control client — pushes configuration to cameras via their control API.

Uses server mTLS credentials to authenticate with the camera's HTTPS
status server. The camera verifies the server certificate against the
CA established during pairing (ADR-0009, ADR-0015).

Design patterns:
- Constructor Injection (certs_dir injected)
- Fail-Graceful (returns error instead of raising)
"""

import json
import logging
import os
import ssl
from http import client as http_client

from monitor.services.camera_trust import (
    load_pinned_status_cert,
    persist_pinned_status_cert,
    status_cert_fingerprint_from_der,
)

log = logging.getLogger("monitor.camera_control_client")

# Timeout for control API requests (seconds)
REQUEST_TIMEOUT = 15
CONTROL_PORT = 8443
CERT_MISMATCH_ERROR = "Camera certificate mismatch — re-pair required"


class CameraControlClient:
    """Push configuration to cameras via their control API.

    Args:
        certs_dir: Path to server certificate directory containing
            server.crt, server.key, and ca.crt.
        control_port: HTTPS control listener port on the camera.
        pin_provider: Callable(camera_id) -> stored fingerprint or "".
        pin_recorder: Callable(camera_id, fingerprint) invoked on a TOFU pin.
        audit: Optional audit logger for CONTROL_TOFU_PIN events.
    """

    def __init__(
        self,
        certs_dir,
        control_port=CONTROL_PORT,
        pin_provider=None,
        pin_recorder=None,
        audit=None,
    ):
        self._certs_dir = certs_dir
        self._control_port = control_port
        self._pin_provider = pin_provider or (lambda _camera_id: "")
        self._pin_recorder = pin_recorder
        self._audit = audit

    def _base_ssl_context(self):
        """Build a client TLS context with the server cert chain loaded."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False

        cert_path = os.path.join(self._certs_dir, "server.crt")
        key_path = os.path.join(self._certs_dir, "server.key")
        if os.path.isfile(cert_path) and os.path.isfile(key_path):
            ctx.load_cert_chain(cert_path, key_path)
        return ctx

    def _ssl_context(self, camera_id):
        """Build a verifying TLS context for a camera with a pinned cert."""
        ctx = self._base_ssl_context()
        pinned_cert = load_pinned_status_cert(self._certs_dir, camera_id)
        if not pinned_cert:
            raise FileNotFoundError(f"No pinned status certificate for {camera_id}")
        ctx.load_verify_locations(cadata=pinned_cert)
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def get_config(self, camera_ip, camera_id=""):
        """GET /api/v1/control/config from camera.

        Returns (config_dict, error_string).
        """
        return self._request(
            "GET", camera_ip, "/api/v1/control/config", camera_id=camera_id
        )

    def set_config(self, camera_ip, params, request_id=0, camera_id=""):
        """PUT /api/v1/control/config on camera.

        Args:
            camera_ip: Camera IP address.
            params: Dict of parameter names to new values.
            request_id: Monotonic request ID for replay protection.
            camera_id: Camera ID used for cert pin lookup/persistence.

        Returns (result_dict, error_string).
        """
        body = dict(params)
        if request_id:
            body["request_id"] = request_id
        return self._request(
            "PUT",
            camera_ip,
            "/api/v1/control/config",
            body,
            camera_id=camera_id,
        )

    def get_capabilities(self, camera_ip, camera_id=""):
        """GET /api/v1/control/capabilities from camera.

        Returns (capabilities_dict, error_string).
        """
        return self._request(
            "GET",
            camera_ip,
            "/api/v1/control/capabilities",
            camera_id=camera_id,
        )

    def get_status(self, camera_ip, camera_id=""):
        """GET /api/v1/control/status from camera.

        Returns (status_dict, error_string).
        """
        return self._request(
            "GET", camera_ip, "/api/v1/control/status", camera_id=camera_id
        )

    def restart_stream(self, camera_ip, camera_id=""):
        """POST /api/v1/control/restart-stream on camera.

        Returns (result_dict, error_string).
        """
        return self._request(
            "POST",
            camera_ip,
            "/api/v1/control/restart-stream",
            camera_id=camera_id,
        )

    def start_stream(self, camera_ip, camera_id=""):
        """POST /api/v1/control/stream/start on camera (ADR-0017).

        Idempotent: returns success if camera reports already running.
        Returns (result_dict, error_string).
        """
        result, err = self._request(
            "POST",
            camera_ip,
            "/api/v1/control/stream/start",
            {},
            camera_id=camera_id,
        )
        if not err and isinstance(result, dict):
            state = result.get("state")
            if state == "running":
                log.debug("Camera %s stream state=running (start)", camera_ip)
        return result, err

    def stop_stream(self, camera_ip, camera_id=""):
        """POST /api/v1/control/stream/stop on camera (ADR-0017).

        Idempotent: returns success if camera reports already stopped.
        Returns (result_dict, error_string).
        """
        result, err = self._request(
            "POST",
            camera_ip,
            "/api/v1/control/stream/stop",
            {},
            camera_id=camera_id,
        )
        if not err and isinstance(result, dict):
            state = result.get("state")
            if state == "stopped":
                log.debug("Camera %s stream state=stopped (stop)", camera_ip)
        return result, err

    def get_stream_state(self, camera_ip, camera_id=""):
        """GET /api/v1/control/stream/state from camera (ADR-0017).

        Returns (state_dict, error_string). state_dict contains 'state'.
        """
        return self._request(
            "GET",
            camera_ip,
            "/api/v1/control/stream/state",
            camera_id=camera_id,
        )

    def _pinned_fingerprint(self, camera_id):
        """Return the stored peer-cert fingerprint for a camera."""
        try:
            return (self._pin_provider(camera_id) or "").strip().lower()
        except Exception as exc:
            log.warning("Pin lookup failed for %s: %s", camera_id, exc)
            return ""

    def _record_tofu_pin(self, camera_id, fingerprint):
        """Persist a TOFU pin into the camera store."""
        if not self._pin_recorder:
            raise RuntimeError(f"No TOFU pin recorder configured for {camera_id}")
        self._pin_recorder(camera_id, fingerprint)

    def _log_tofu_pin(self, camera_id, fingerprint):
        """Audit a one-shot control-channel TOFU pin."""
        if not self._audit:
            return
        try:
            self._audit.log_event(
                "CONTROL_TOFU_PIN",
                user="system",
                ip="",
                detail=f"camera {camera_id} fingerprint {fingerprint}",
            )
        except Exception as exc:
            log.warning("Audit log failed for CONTROL_TOFU_PIN: %s", exc)

    def _trusted_headers(self, data):
        return {"Content-Type": "application/json"} if data else {}

    def _handle_response(self, conn, method, url):
        """Parse an HTTP response from the camera control API."""
        resp = conn.getresponse()
        resp_body = resp.read()
        if 200 <= resp.status < 300:
            result = json.loads(resp_body) if resp_body else {}
            return result, ""

        try:
            err_body = json.loads(resp_body) if resp_body else {}
            err_msg = err_body.get("error", f"HTTP {resp.status}")
        except json.JSONDecodeError:
            err_msg = f"HTTP {resp.status}"
        log.warning("Control request %s %s failed: %s", method, url, err_msg)
        return None, err_msg

    def _verified_request(self, method, camera_id, camera_ip, path, data):
        """Send a control request using a pinned, verifying TLS context."""
        url = f"https://{camera_ip}:{self._control_port}{path}"
        conn = http_client.HTTPSConnection(
            camera_ip,
            self._control_port,
            context=self._ssl_context(camera_id),
            timeout=REQUEST_TIMEOUT,
        )
        try:
            conn.request(method, path, body=data, headers=self._trusted_headers(data))
            return self._handle_response(conn, method, url)
        finally:
            conn.close()

    def _bootstrap_request(
        self,
        method,
        camera_id,
        camera_ip,
        path,
        data,
        expected_fingerprint,
    ):
        """Connect without peer verification to pin or recover the pinned cert."""
        url = f"https://{camera_ip}:{self._control_port}{path}"
        ctx = self._base_ssl_context()
        ctx.verify_mode = ssl.CERT_NONE
        conn = http_client.HTTPSConnection(
            camera_ip, self._control_port, context=ctx, timeout=REQUEST_TIMEOUT
        )
        try:
            conn.connect()
            peer_der = conn.sock.getpeercert(binary_form=True) if conn.sock else None
            if not peer_der:
                return None, CERT_MISMATCH_ERROR

            peer_fingerprint = status_cert_fingerprint_from_der(peer_der)
            peer_pem = ssl.DER_cert_to_PEM_cert(peer_der)

            if expected_fingerprint:
                if peer_fingerprint != expected_fingerprint:
                    log.warning(
                        "Control request %s %s peer fingerprint mismatch for %s",
                        method,
                        url,
                        camera_id,
                    )
                    return None, CERT_MISMATCH_ERROR
                persist_pinned_status_cert(self._certs_dir, camera_id, peer_pem)
            else:
                persist_pinned_status_cert(self._certs_dir, camera_id, peer_pem)
                self._record_tofu_pin(camera_id, peer_fingerprint)
                self._log_tofu_pin(camera_id, peer_fingerprint)

            conn.request(method, path, body=data, headers=self._trusted_headers(data))
            return self._handle_response(conn, method, url)
        finally:
            conn.close()

    def _request(self, method, camera_ip, path, body=None, camera_id=""):
        """Make an HTTPS request to the camera's control API.

        Returns (response_dict, error_string). Error is empty on success.
        """
        data = None
        if body is not None:
            data = json.dumps(body).encode()
        if not camera_id:
            return None, "Camera ID required for authenticated control request"

        try:
            expected_fingerprint = self._pinned_fingerprint(camera_id)
            pinned_cert = load_pinned_status_cert(self._certs_dir, camera_id)
            if expected_fingerprint and pinned_cert:
                return self._verified_request(method, camera_id, camera_ip, path, data)
            return self._bootstrap_request(
                method,
                camera_id,
                camera_ip,
                path,
                data,
                expected_fingerprint,
            )
        except ssl.SSLCertVerificationError as exc:
            log.warning(
                "Control request %s https://%s:%s%s peer verification failed: %s",
                method,
                camera_ip,
                self._control_port,
                path,
                exc,
            )
            return None, CERT_MISMATCH_ERROR
        except ssl.SSLError as exc:
            log.warning(
                "Control request %s https://%s:%s%s TLS error: %s",
                method,
                camera_ip,
                self._control_port,
                path,
                exc,
            )
            if self._pinned_fingerprint(camera_id):
                return None, CERT_MISMATCH_ERROR
            return None, f"Camera unreachable: {exc}"
        except (
            OSError,
            http_client.HTTPException,
            json.JSONDecodeError,
            RuntimeError,
        ) as e:
            log.warning(
                "Control request %s https://%s:%s%s error: %s",
                method,
                camera_ip,
                self._control_port,
                path,
                e,
            )
            if _is_control_port_unreachable(e):
                return None, "camera control port unreachable (firmware mismatch?)"
            return None, str(e)


def _is_control_port_unreachable(reason) -> bool:
    """Detect the "new port not listening yet" upgrade-mismatch case."""
    if isinstance(reason, ConnectionRefusedError):
        return True
    if isinstance(reason, OSError) and getattr(reason, "errno", None) in {
        111,
        61,
        10061,
    }:
        return True
    return "connection refused" in str(reason).lower()
