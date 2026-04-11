"""
API contract tests — verify exact response field names for camera endpoints.

Mirrors the server's test_api_contracts.py approach. Camera has two HTTP
servers with JSON APIs:

1. WiFi Setup Server (first boot, no auth)
2. Status Server (post-setup, auth required)

Uses a high port (18080) to avoid requiring root on CI.

Layer 4 of the testing pyramid (see docs/development-guide.md Section 3.8).
"""

import json
from unittest.mock import patch
from urllib.request import Request, urlopen

import pytest

from camera_streamer.config import ConfigManager
from camera_streamer.status_server import CameraStatusServer
from camera_streamer.wifi_setup import WifiSetupServer

# Use a non-privileged port for CI (port 80 requires root on Linux)
TEST_PORT = 18080


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _json_get(path):
    """GET a JSON endpoint on localhost:TEST_PORT."""
    req = Request(f"http://127.0.0.1:{TEST_PORT}{path}")
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read()), resp.status


def _json_post(path, body, headers=None):
    """POST JSON to an endpoint on localhost:TEST_PORT."""
    data = json.dumps(body).encode()
    req = Request(
        f"http://127.0.0.1:{TEST_PORT}{path}",
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()), resp.status
    except Exception as e:
        # urllib raises on 4xx/5xx — read the error body
        if hasattr(e, "read"):
            return json.loads(e.read()), e.code
        raise


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
    """ConfigManager without password (no auth needed for status server)."""
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

SETUP_STATUS_FIELDS = {"status", "error", "setup_complete", "camera_id", "hostname"}
NETWORK_FIELDS = {"ssid", "signal", "security"}
CONNECT_SUCCESS_FIELDS = {"status", "message"}


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

    @patch("camera_streamer.wifi.get_hostname", return_value="cam-test")
    @patch("camera_streamer.wifi.scan_networks", return_value=[])
    @patch("camera_streamer.wifi.start_hotspot", return_value=True)
    def test_response_fields(self, mock_hotspot, mock_scan, mock_host, setup_config):
        server = WifiSetupServer(setup_config)
        server.start()
        try:
            data, status = _json_get("/api/status")
            _assert_fields(data, SETUP_STATUS_FIELDS)
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
                    "admin_password": "testpass",
                },
            )
            _assert_fields(data, CONNECT_SUCCESS_FIELDS)
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
    "cpu_temp",
    "uptime",
    "memory_total_mb",
    "memory_used_mb",
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
            data, status = _json_get("/api/status")
            _assert_fields(data, STATUS_API_FIELDS)
        finally:
            server.stop()


class TestStatusServerNetworksContract:
    """GET /api/networks on status server."""

    @patch("camera_streamer.status_server.wifi.scan_networks")
    def test_fields(self, mock_scan, noauth_config):
        mock_scan.return_value = [
            {"ssid": "Net1", "signal": 70, "security": "WPA2"},
        ]
        server = CameraStatusServer(noauth_config)
        server.start()
        try:
            data, status = _json_get("/api/networks")
            _assert_fields(data, {"networks"})
            assert isinstance(data["networks"], list)
        finally:
            server.stop()


class TestStatusServerWifiContract:
    """POST /api/wifi on status server."""

    @patch("camera_streamer.status_server.wifi.connect_network")
    def test_success_fields(self, mock_connect, noauth_config):
        mock_connect.return_value = (True, None)
        server = CameraStatusServer(noauth_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/wifi", {"ssid": "NewNet", "password": "pass123"}
            )
            _assert_has_fields(data, {"message"})
        finally:
            server.stop()

    @patch("camera_streamer.status_server.wifi.connect_network")
    def test_error_missing_ssid(self, mock_connect, noauth_config):
        server = CameraStatusServer(noauth_config)
        server.start()
        try:
            data, status = _json_post("/api/wifi", {"ssid": "", "password": "pass"})
            _assert_fields(data, {"error"})
        finally:
            server.stop()


class TestStatusServerPasswordContract:
    """POST /api/password on status server."""

    def test_error_fields_short_password(self, noauth_config):
        """Password too short should return {error}."""
        # Set a password so change endpoint can validate current one
        noauth_config.set_password("oldpass")
        noauth_config.save()

        server = CameraStatusServer(noauth_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/password",
                {"current_password": "oldpass", "new_password": "ab"},
            )
            _assert_fields(data, {"error"})
        finally:
            server.stop()


class TestStatusServerLoginContract:
    """POST /login (JSON mode) on status server."""

    def test_error_fields(self, configured_config):
        """Invalid login returns {error}."""
        server = CameraStatusServer(configured_config)
        server.start()
        try:
            data, status = _json_post(
                "/login",
                {"username": "wrong", "password": "wrong"},
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
            )
            _assert_fields(data, {"message"})
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
