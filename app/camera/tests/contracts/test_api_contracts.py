# REQ: SWR-045; RISK: RISK-021; SEC: SC-021; TEST: TC-042
"""
API contract tests — verify exact response field names for camera endpoints.

Mirrors the server's test_api_contracts.py approach. Camera has two HTTP
servers with JSON APIs:

1. WiFi Setup Server (first boot, no auth)
2. Status Server (post-setup, auth required)

Uses a high port (18080) to avoid requiring root on CI.

Layer 4 of the testing pyramid (see docs/guides/development-guide.md Section 3.8).
"""

import json
import logging
import ssl
from unittest.mock import patch
from urllib.request import Request, urlopen

import pytest
from tests.fixtures.tls_material import TEST_TLS_CERT, TEST_TLS_KEY

from camera_streamer.config import ConfigManager
from camera_streamer.control import ControlHandler
from camera_streamer.sensor_info import capabilities_for_testing
from camera_streamer.status_server import (
    CameraStatusServer,
    _build_session_cookie,
    _create_session,
    _login_attempt_lock,
    _login_attempts,
    _session_lock,
    _sessions,
)
from camera_streamer.wifi_setup import WifiSetupServer

# Use a non-privileged port for CI (port 80 requires root on Linux)
TEST_PORT = 18080
TLS_CONTEXT = ssl._create_unverified_context()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _status_server_test_tls(monkeypatch, tmp_path):
    """Use static test TLS material for API-contract server startups."""
    cert_path = tmp_path / "status-test.crt"
    key_path = tmp_path / "status-test.key"
    cert_path.write_text(TEST_TLS_CERT, encoding="ascii")
    key_path.write_text(TEST_TLS_KEY, encoding="ascii")
    monkeypatch.setattr(
        "camera_streamer.status_server._ensure_tls_material",
        lambda _config: (str(cert_path), str(key_path)),
    )


def _assert_fields(data, required_fields, msg=""):
    """Assert data dict contains exactly the required top-level keys."""
    actual = set(data.keys())
    missing = required_fields - actual
    extra = actual - required_fields
    assert not missing, f"Missing fields {missing}. {msg}"
    assert not extra, f"Unexpected fields {extra}. {msg}"


def _assert_has_fields(data, required_fields, msg=""):
    """Assert data dict contains at least the required keys."""
    actual = set(data.keys())
    missing = required_fields - actual
    assert not missing, f"Missing fields {missing}. {msg}"


def _json_get(path, scheme="http", headers=None):
    """GET a JSON endpoint on localhost:TEST_PORT."""
    req = Request(f"{scheme}://127.0.0.1:{TEST_PORT}{path}", headers=headers or {})
    kwargs = {"timeout": 5}
    if scheme == "https":
        kwargs["context"] = TLS_CONTEXT
    try:
        with urlopen(req, **kwargs) as resp:
            return json.loads(resp.read()), resp.status
    except Exception as e:
        if hasattr(e, "read"):
            return json.loads(e.read()), e.code
        raise


def _json_post(path, body, headers=None, scheme="http"):
    """POST JSON to an endpoint on localhost:TEST_PORT."""
    data = json.dumps(body).encode()
    req = Request(
        f"{scheme}://127.0.0.1:{TEST_PORT}{path}",
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    kwargs = {"timeout": 5}
    if scheme == "https":
        kwargs["context"] = TLS_CONTEXT
    try:
        with urlopen(req, **kwargs) as resp:
            return json.loads(resp.read()), resp.status
    except Exception as e:
        # urllib raises on 4xx/5xx — read the error body
        if hasattr(e, "read"):
            return json.loads(e.read()), e.code
        raise


def _json_put(path, body, headers=None, scheme="http"):
    """PUT JSON to an endpoint on localhost:TEST_PORT."""
    data = json.dumps(body).encode()
    req = Request(
        f"{scheme}://127.0.0.1:{TEST_PORT}{path}",
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="PUT",
    )
    kwargs = {"timeout": 5}
    if scheme == "https":
        kwargs["context"] = TLS_CONTEXT
    try:
        with urlopen(req, **kwargs) as resp:
            return json.loads(resp.read()), resp.status
    except Exception as e:
        if hasattr(e, "read"):
            return json.loads(e.read()), e.code
        raise


def _head(path, scheme="http", headers=None):
    """HEAD request to localhost:TEST_PORT."""
    req = Request(
        f"{scheme}://127.0.0.1:{TEST_PORT}{path}",
        headers=headers or {},
        method="HEAD",
    )
    kwargs = {"timeout": 5}
    if scheme == "https":
        kwargs["context"] = TLS_CONTEXT
    with urlopen(req, **kwargs) as resp:
        return resp.status


def _make_control_handler(config):
    return ControlHandler(
        config,
        None,
        sensor_capabilities=capabilities_for_testing("ov5647"),
    )


def _html_get(path, scheme="http", headers=None):
    """GET an HTML page on localhost:TEST_PORT."""
    req = Request(f"{scheme}://127.0.0.1:{TEST_PORT}{path}", headers=headers or {})
    kwargs = {"timeout": 5}
    if scheme == "https":
        kwargs["context"] = TLS_CONTEXT
    with urlopen(req, **kwargs) as resp:
        return resp.read().decode("utf-8"), resp.status


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_listen_port():
    """Patch LISTEN_PORT to a non-privileged port for all contract tests."""
    with (
        patch("camera_streamer.wifi_setup.LISTEN_PORT", TEST_PORT),
        patch("camera_streamer.status_server.LISTEN_PORT", TEST_PORT),
    ):
        yield


@pytest.fixture(autouse=True)
def _clear_status_auth_state():
    """Keep status-server sessions and lockouts isolated between tests."""
    with _session_lock:
        _sessions.clear()
    with _login_attempt_lock:
        _login_attempts.clear()
    yield
    with _session_lock:
        _sessions.clear()
    with _login_attempt_lock:
        _login_attempts.clear()


def _auth_headers():
    token = _create_session()
    return {"Cookie": _build_session_cookie(token).split(";", 1)[0]}


@pytest.fixture
def setup_config(tmp_path):
    """ConfigManager that needs setup (no server IP)."""
    (tmp_path / "config").mkdir()
    (tmp_path / "certs").mkdir()
    (tmp_path / "logs").mkdir()
    mgr = ConfigManager(data_dir=str(tmp_path))
    mgr.load()
    return mgr


@pytest.fixture
def configured_config(tmp_path):
    """ConfigManager with password set (auth required)."""
    (tmp_path / "config").mkdir()
    (tmp_path / "certs").mkdir()
    (tmp_path / "logs").mkdir()
    config_file = tmp_path / "config" / "camera.conf"
    config_file.write_text(
        "SERVER_IP=192.168.1.100\n"
        "SERVER_PORT=8554\n"
        "STREAM_NAME=stream\n"
        "WIDTH=1920\n"
        "HEIGHT=1080\n"
        "FPS=25\n"
        "CAMERA_ID=cam-contract01\n"
    )
    mgr = ConfigManager(data_dir=str(tmp_path))
    mgr.load()
    mgr.set_password("testpass")
    mgr.save()
    return mgr


@pytest.fixture
def noauth_config(tmp_path):
    """ConfigManager without password for fail-closed status-server tests."""
    (tmp_path / "config").mkdir()
    (tmp_path / "certs").mkdir()
    (tmp_path / "logs").mkdir()
    config_file = tmp_path / "config" / "camera.conf"
    config_file.write_text(
        "SERVER_IP=192.168.1.100\n"
        "SERVER_PORT=8554\n"
        "STREAM_NAME=stream\n"
        "WIDTH=1920\n"
        "HEIGHT=1080\n"
        "FPS=25\n"
        "CAMERA_ID=cam-contract01\n"
    )
    mgr = ConfigManager(data_dir=str(tmp_path))
    mgr.load()
    return mgr


# ===========================================================================
# WiFi Setup Server contracts
# ===========================================================================

SETUP_STATUS_FIELDS = {
    "status",
    "error",
    "setup_complete",
    "camera_id",
    "hostname",
    "ip_address",
    "server_address",
}
NETWORK_FIELDS = {"ssid", "signal", "security"}
CONNECT_SUCCESS_FIELDS = {"status", "message", "hostname"}


class TestSetupNetworksContract:
    """GET /api/networks on setup server."""

    @patch("camera_streamer.wifi.scan_networks")
    @patch("camera_streamer.wifi.start_hotspot")
    def test_response_fields(self, mock_hotspot, mock_scan, setup_config):
        mock_scan.return_value = [
            {"ssid": "TestNet", "signal": 75, "security": "WPA2"},
        ]
        mock_hotspot.return_value = True

        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_get("/api/networks")
            _assert_fields(data, {"networks"})
            assert isinstance(data["networks"], list)
            if data["networks"]:
                _assert_fields(data["networks"][0], NETWORK_FIELDS)
        finally:
            server.stop()


class TestSetupStatusContract:
    """GET /api/status on setup server."""

    @patch("camera_streamer.wifi.get_ip_address", return_value="192.168.1.42")
    @patch("camera_streamer.wifi.get_hostname", return_value="cam-test")
    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_response_fields(
        self,
        mock_hotspot,
        mock_scan,
        mock_host,
        mock_ip,
        setup_config,
    ):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_get("/api/status")
            _assert_fields(data, SETUP_STATUS_FIELDS)
            assert data["ip_address"] == "192.168.1.42"
        finally:
            server.stop()

    @patch("camera_streamer.wifi.get_hostname", return_value="cam-test")
    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_head_setup_page(self, mock_hotspot, mock_scan, mock_host, setup_config):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            assert _head("/") == 200
        finally:
            server.stop()

    @patch("camera_streamer.wifi.get_ip_address", side_effect=RuntimeError("boom"))
    @patch("camera_streamer.wifi.get_hostname", return_value="cam-test")
    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_ip_address_falls_back_to_empty_string(
        self,
        mock_hotspot,
        mock_scan,
        mock_host,
        mock_ip,
        setup_config,
    ):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_get("/api/status")
            assert data["ip_address"] == ""
        finally:
            server.stop()

    @patch("camera_streamer.wifi.get_hostname", return_value="cam-test")
    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_setup_page_renders_reachability_block(
        self, mock_hotspot, mock_scan, mock_host, setup_config
    ):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            html, status = _html_get("/setup")
            assert status == 200
            assert "Reach this camera" in html
            assert "/static/qrcode.min.js" in html
        finally:
            server.stop()


class TestSetupConnectContract:
    """POST /api/connect on setup server."""

    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_success_fields(self, mock_hotspot, mock_scan, setup_config):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/connect",
                {
                    "ssid": "TestNet",
                    "password": "pass123",
                    "server_ip": "192.168.1.100",
                    "admin_username": "admin",
                    "admin_password": "testpass12345",
                },
            )
            _assert_fields(data, CONNECT_SUCCESS_FIELDS)
        finally:
            server.stop()

    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_rejects_short_admin_password(self, mock_hotspot, mock_scan, setup_config):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/connect",
                {
                    "ssid": "TestNet",
                    "password": "pass123",
                    "server_ip": "192.168.1.100",
                    "admin_username": "admin",
                    "admin_password": "short",
                },
            )
            assert status == 400
            _assert_fields(data, {"error"})
            assert "12" in data["error"]
        finally:
            server.stop()

    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_error_fields(self, mock_hotspot, mock_scan, setup_config):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/connect", {"ssid": "", "password": "pass123"}
            )
            _assert_fields(data, {"error"})
        finally:
            server.stop()

    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_missing_server_ip_error(self, mock_hotspot, mock_scan, setup_config):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/connect",
                {"ssid": "Net", "password": "pass", "server_ip": ""},
            )
            _assert_fields(data, {"error"})
        finally:
            server.stop()


class TestSetupRescanContract:
    """POST /api/rescan on setup server."""

    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    @patch("camera_streamer.wifi.stop_hotspot")
    @patch("camera_streamer.wifi.scan_networks")
    def test_response_fields(self, mock_scan, mock_stop, mock_start, setup_config):
        mock_scan.return_value = [
            {"ssid": "Net1", "signal": 80, "security": "WPA2"},
        ]
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_post("/api/rescan", {})
            _assert_fields(data, {"networks"})
            assert isinstance(data["networks"], list)
        finally:
            server.stop()


# ===========================================================================
# Status Server contracts
# ===========================================================================

STATUS_API_FIELDS = {
    "camera_id",
    "hostname",
    "ip_address",
    "wifi_ssid",
    "server_address",
    "server_connected",
    "streaming",
    "paired",
    "firmware_version",
    "cpu_temp",
    "uptime",
    "memory_total_mb",
    "memory_used_mb",
    "stream_config",
    # Hardware health surfaces the "no camera module detected" banner
    # on both the camera's own status page and the server dashboard.
    "hardware_ok",
    "hardware_error",
}


class TestStatusServerApiStatusContract:
    """GET /api/status on status server."""

    @patch(
        "camera_streamer.status_server.wifi.get_ip_address",
        return_value="192.168.1.50",
    )
    @patch(
        "camera_streamer.status_server.wifi.get_current_ssid",
        return_value="HomeNet",
    )
    @patch(
        "camera_streamer.status_server.wifi.get_hostname",
        return_value="cam-test",
    )
    @patch("camera_streamer.status_server._get_memory_mb", return_value=(512, 256))
    @patch("camera_streamer.status_server._get_uptime", return_value="1h 30m")
    @patch("camera_streamer.status_server._get_cpu_temp", return_value=45.0)
    def test_fields_no_auth(
        self,
        mock_temp,
        mock_uptime,
        mock_mem,
        mock_host,
        mock_ssid,
        mock_ip,
        noauth_config,
    ):
        """When no password set, /api/status doesn't need auth."""
        server = CameraStatusServer(
            noauth_config, stream_manager=None, wifi_interface="wlan0"
        )
        server.start()
        try:
            data, status = _json_get("/api/status", scheme="https")
            _assert_fields(data, STATUS_API_FIELDS)
        finally:
            server.stop()

    @patch(
        "camera_streamer.status_server.wifi.get_ip_address",
        return_value="192.168.1.50",
    )
    @patch(
        "camera_streamer.status_server.wifi.get_current_ssid",
        return_value="HomeNet",
    )
    @patch(
        "camera_streamer.status_server.wifi.get_hostname",
        return_value="cam-test",
    )
    def test_status_page_renders_local_control_panel(
        self, mock_host, mock_ssid, mock_ip, configured_config
    ):
        server = CameraStatusServer(
            configured_config, stream_manager=None, wifi_interface="wlan0"
        )
        server.start()
        try:
            html, status = _html_get(
                "/status", scheme="https", headers=_auth_headers()
            )
            assert status == 200
            assert 'aria-label="Page sections"' in html
            for nav_target in [
                'href="#status"',
                'href="#settings"',
                'href="#updates"',
                'href="#danger"',
            ]:
                assert nav_target in html
            for anchor in [
                'id="hardware-alert"',
                'id="hero-line"',
                'id="h-server"',
                'id="pair-cta"',
                'id="btn-pair"',
                'id="btn-scan"',
                'id="wifi-ssid"',
                'id="btn-wifi"',
                'id="btn-pw"',
                'id="btn-stream-edit"',
                'id="se-res"',
                'id="btn-stream-save"',
                'id="unpair-details"',
                'id="btn-repair"',
                'id="btn-unpair"',
                'id="ota-file"',
                'id="btn-ota-upload"',
                'id="reset-confirm"',
                'id="btn-reset"',
            ]:
                assert anchor in html
            assert "Reach this camera" not in html
            assert "/static/qrcode.min.js" not in html
        finally:
            server.stop()


class TestStatusServerNoPasswordAuthContract:
    """No-password post-setup cameras fail closed for admin operations."""

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("GET", "/api/networks", None),
            ("GET", "/api/ota/status", None),
            ("PUT", "/api/stream-config", {"fps": 20}),
            ("POST", "/api/ota/upload", {"bundle": "x"}),
            ("POST", "/api/ota/reboot", {}),
            ("POST", "/api/wifi", {"ssid": "NewNet", "password": "pass123"}),
            ("POST", "/api/factory-reset", {}),
            ("POST", "/api/unpair", {}),
            (
                "POST",
                "/api/password",
                {"current_password": "old", "new_password": "testpass12345"},
            ),
        ],
    )
    def test_privileged_routes_fail_closed_without_password(
        self, method, path, body, noauth_config
    ):
        server = CameraStatusServer(
            noauth_config,
            control_handler=_make_control_handler(noauth_config),
        )
        server.start()
        try:
            if method == "GET":
                data, status = _json_get(path, scheme="https")
            elif method == "PUT":
                data, status = _json_put(path, body, scheme="https")
            else:
                data, status = _json_post(path, body, scheme="https")
            assert status == 503
            _assert_fields(data, {"error"})
        finally:
            server.stop()


class TestStatusServerNetworksContract:
    """GET /api/networks on status server."""

    @patch("camera_streamer.status_server.wifi.scan_networks")
    def test_fields(self, mock_scan, configured_config):
        mock_scan.return_value = [
            {"ssid": "Net1", "signal": 70, "security": "WPA2"},
        ]
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            data, status = _json_get(
                "/api/networks", scheme="https", headers=_auth_headers()
            )
            _assert_fields(data, {"networks"})
            assert isinstance(data["networks"], list)
        finally:
            server.stop()


class TestStatusServerWifiContract:
    """POST /api/wifi on status server."""

    @patch("camera_streamer.status_server.wifi.connect_network")
    def test_success_fields(self, mock_connect, configured_config):
        mock_connect.return_value = (True, None)
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/wifi",
                {"ssid": "NewNet", "password": "pass123"},
                headers=_auth_headers(),
                scheme="https",
            )
            _assert_has_fields(data, {"message"})
        finally:
            server.stop()

    @patch("camera_streamer.status_server.wifi.connect_network")
    def test_error_missing_ssid(self, mock_connect, configured_config):
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/wifi",
                {"ssid": "", "password": "pass"},
                headers=_auth_headers(),
                scheme="https",
            )
            _assert_fields(data, {"error"})
        finally:
            server.stop()


class TestStatusServerPasswordContract:
    """POST /api/password on status server."""

    def test_error_fields_short_password(self, configured_config):
        """Password too short should return {error}."""
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/password",
                {"current_password": "testpass", "new_password": "ab"},
                headers=_auth_headers(),
                scheme="https",
            )
            _assert_fields(data, {"error"})
        finally:
            server.stop()


class TestStatusServerLoginContract:
    """POST /login (JSON mode) on status server."""

    def test_no_password_login_fails_closed(self, noauth_config):
        """Missing ADMIN_PASSWORD never creates an authenticated session."""
        server = CameraStatusServer(noauth_config)
        server.start()
        try:
            data, status = _json_post(
                "/login",
                {"username": "admin", "password": "anything"},
                scheme="https",
            )
            assert status == 503
            _assert_fields(data, {"error"})
        finally:
            server.stop()

    def test_error_fields(self, configured_config):
        """Invalid login returns {error}."""
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            data, status = _json_post(
                "/login",
                {"username": "wrong", "password": "wrong"},
                scheme="https",
            )
            _assert_fields(data, {"error"})
        finally:
            server.stop()

    def test_success_fields(self, configured_config):
        """Valid login returns {message}."""
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            data, status = _json_post(
                "/login",
                {"username": "admin", "password": "testpass"},
                scheme="https",
            )
            _assert_fields(data, {"message"})
        finally:
            server.stop()

    def test_lockout_fields(self, configured_config, caplog):
        """Repeated invalid logins return a 429 with {error}."""
        caplog.set_level(logging.WARNING, logger="camera-streamer.status-server")
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            status = None
            data = {}
            for _ in range(5):
                data, status = _json_post(
                    "/login",
                    {"username": "admin", "password": "wrong"},
                    scheme="https",
                )
            assert status == 429
            _assert_fields(data, {"error"})
            assert "Camera admin login lockout started" in caplog.text
        finally:
            server.stop()

    def test_head_root_redirects_or_serves(self, configured_config):
        """HEAD / should not fail for browser/probe checks."""
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            status = _head("/", scheme="https")
            assert status in {200, 302}
        finally:
            server.stop()


STREAM_CONFIG_SUCCESS_FIELDS = {
    "applied",
    "restart_required",
    "restarted",
    "status",
    "origin",
}


class TestStatusServerStreamConfigContract:
    """PUT /api/stream-config on status server (session auth)."""

    def test_authenticated_success_fields(self, configured_config):
        """Authenticated /api/stream-config returns applied config fields."""
        server = CameraStatusServer(
            configured_config,
            control_handler=_make_control_handler(configured_config),
        )
        server.start()
        try:
            data, status = _json_put(
                "/api/stream-config",
                {"fps": 20},
                headers=_auth_headers(),
                scheme="https",
            )
            _assert_has_fields(data, {"applied", "status"})
        finally:
            server.stop()

    def test_error_on_invalid_param(self, configured_config):
        """Invalid param returns {error}."""
        server = CameraStatusServer(
            configured_config,
            control_handler=_make_control_handler(configured_config),
        )
        server.start()
        try:
            data, status = _json_put(
                "/api/stream-config",
                {"unknown_field": 42},
                headers=_auth_headers(),
                scheme="https",
            )
            _assert_fields(data, {"error"})
        finally:
            server.stop()


# ===========================================================================
# Error response consistency
# ===========================================================================


class TestErrorResponseConsistency:
    """All camera error responses use {"error": "..."} format."""

    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_setup_validation_errors_have_error_field(
        self, mock_hotspot, mock_scan, setup_config
    ):
        """Setup POST /api/connect validation returns {error}."""
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            # Missing SSID
            data, _ = _json_post("/api/connect", {"ssid": "", "password": "x"})
            assert "error" in data
            assert isinstance(data["error"], str)

            # Missing password
            data, _ = _json_post("/api/connect", {"ssid": "Net", "password": ""})
            assert "error" in data

            # Missing server IP
            data, _ = _json_post(
                "/api/connect",
                {"ssid": "Net", "password": "pass", "server_ip": ""},
            )
            assert "error" in data
        finally:
            server.stop()
