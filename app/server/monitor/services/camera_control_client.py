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
import urllib.error
import urllib.request

log = logging.getLogger("monitor.camera_control_client")

# Timeout for control API requests (seconds)
REQUEST_TIMEOUT = 15


class CameraControlClient:
    """Push configuration to cameras via their control API.

    Args:
        certs_dir: Path to server certificate directory containing
            server.crt, server.key, and ca.crt.
    """

    def __init__(self, certs_dir):
        self._certs_dir = certs_dir

    def _ssl_context(self):
        """Build an SSL context with server mTLS credentials.

        The server presents its certificate (signed by the CA) to the
        camera, which verifies it against ca.crt received during pairing.
        We disable hostname verification because the camera's status
        server uses a self-signed cert with its own hostname.
        """
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # camera uses self-signed status cert

        # Load server's mTLS client credentials
        cert_path = os.path.join(self._certs_dir, "server.crt")
        key_path = os.path.join(self._certs_dir, "server.key")
        if os.path.isfile(cert_path) and os.path.isfile(key_path):
            ctx.load_cert_chain(cert_path, key_path)

        return ctx

    def get_config(self, camera_ip):
        """GET /api/v1/control/config from camera.

        Returns (config_dict, error_string).
        """
        return self._request("GET", camera_ip, "/api/v1/control/config")

    def set_config(self, camera_ip, params, request_id=0):
        """PUT /api/v1/control/config on camera.

        Args:
            camera_ip: Camera IP address.
            params: Dict of parameter names to new values.
            request_id: Monotonic request ID for replay protection.

        Returns (result_dict, error_string).
        """
        body = dict(params)
        if request_id:
            body["request_id"] = request_id
        return self._request("PUT", camera_ip, "/api/v1/control/config", body)

    def get_capabilities(self, camera_ip):
        """GET /api/v1/control/capabilities from camera.

        Returns (capabilities_dict, error_string).
        """
        return self._request("GET", camera_ip, "/api/v1/control/capabilities")

    def get_status(self, camera_ip):
        """GET /api/v1/control/status from camera.

        Returns (status_dict, error_string).
        """
        return self._request("GET", camera_ip, "/api/v1/control/status")

    def restart_stream(self, camera_ip):
        """POST /api/v1/control/restart-stream on camera.

        Returns (result_dict, error_string).
        """
        return self._request("POST", camera_ip, "/api/v1/control/restart-stream")

    def start_stream(self, camera_ip):
        """POST /api/v1/control/stream/start on camera (ADR-0017).

        Idempotent: returns success if camera reports already running.
        Returns (result_dict, error_string).
        """
        result, err = self._request(
            "POST", camera_ip, "/api/v1/control/stream/start", {}
        )
        if not err and isinstance(result, dict):
            state = result.get("state")
            if state == "running":
                log.debug("Camera %s stream state=running (start)", camera_ip)
        return result, err

    def stop_stream(self, camera_ip):
        """POST /api/v1/control/stream/stop on camera (ADR-0017).

        Idempotent: returns success if camera reports already stopped.
        Returns (result_dict, error_string).
        """
        result, err = self._request(
            "POST", camera_ip, "/api/v1/control/stream/stop", {}
        )
        if not err and isinstance(result, dict):
            state = result.get("state")
            if state == "stopped":
                log.debug("Camera %s stream state=stopped (stop)", camera_ip)
        return result, err

    def get_stream_state(self, camera_ip):
        """GET /api/v1/control/stream/state from camera (ADR-0017).

        Returns (state_dict, error_string). state_dict contains 'state'.
        """
        return self._request("GET", camera_ip, "/api/v1/control/stream/state")

    def _request(self, method, camera_ip, path, body=None):
        """Make an HTTPS request to the camera's control API.

        Returns (response_dict, error_string). Error is empty on success.
        """
        url = f"https://{camera_ip}{path}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )

        try:
            ctx = self._ssl_context()
            with urllib.request.urlopen(
                req, context=ctx, timeout=REQUEST_TIMEOUT
            ) as resp:
                resp_body = resp.read()
                result = json.loads(resp_body) if resp_body else {}
                return result, ""
        except urllib.error.HTTPError as e:
            try:
                err_body = json.loads(e.read())
                err_msg = err_body.get("error", f"HTTP {e.code}")
            except (json.JSONDecodeError, AttributeError):
                err_msg = f"HTTP {e.code}"
            log.warning("Control request %s %s failed: %s", method, url, err_msg)
            return None, err_msg
        except urllib.error.URLError as e:
            log.warning("Control request %s %s unreachable: %s", method, url, e.reason)
            return None, f"Camera unreachable: {e.reason}"
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Control request %s %s error: %s", method, url, e)
            return None, str(e)
