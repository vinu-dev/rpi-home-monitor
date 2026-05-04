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
import ssl
from unittest.mock import patch
from urllib.request import Request, urlopen

import pytest

from camera_streamer.config import ConfigManager
from camera_streamer.control import ControlHandler
from camera_streamer.sensor_info import capabilities_for_testing
from camera_streamer.status_server import CameraStatusServer
from camera_streamer.wifi_setup import WifiSetupServer

# Use a non-privileged port for CI (port 80 requires root on Linux)
TEST_PORT = 18080
TLS_CONTEXT = ssl._create_unverified_context()
TEST_TLS_CERT = """-----BEGIN CERTIFICATE-----
MIIC1DCCAbygAwIBAgIUQJ/0HCgICubUlh5xcLuGlUxBmbEwDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MDUwMzA5MzU1N1oXDTM2MDUw
MTA5MzU1N1owFDESMBAGA1UEAwwJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEF
AAOCAQ8AMIIBCgKCAQEAqmsxl4ovgLFXNoQDt4+ZSZycM2HXdEVhuku3p16mgTZO
CrUFg5FEMV+tHXD4mzUwZkVyjUHWN5P9f0v4tG/t6Zs7uGiPXxRQH3Lrw4XZ7XEt
WwLtlg4Gx8qGSBGl8jC8HSIgr80xky4k6GMPR1FJHfYgSJLe2wBYBUm/JgA5MBI1
UvWSpMR9yXuXejBdWrHZCtNQzclCtfMeq+NTCrz+7T1j+8uz2LxCl91Lf0vEyuZL
b/iEcL56sm18GWF8zrwoQDto1oQAhhhd5Hne5T2S9yAYarLWK/ZK+UBKGB1soxW4
nM4gMN+IfSpqdD85dZyS4BHEyXbzd7M47cqFLIUeeQIDAQABox4wHDAaBgNVHREE
EzARgglsb2NhbGhvc3SHBH8AAAEwDQYJKoZIhvcNAQELBQADggEBAI05ntN0GzQp
0qOn+Tyv/Z8htsXNmafrtgeyYAlMBlCreoZhItG1rAw92JhQ1S3/dZPbKhQDNKi1
WI4LYXpxr/CZYLr+VjslpNAncTcLtiI/VIga837kAinKt2mpBVclNZYMUD7e7K5j
Sp9mNK3F3Yi+FX+hXu7GU2ftd6IZl05LTJXrm29VnqcLahh65NbhTouvl68Jb5La
mykymirwGkxQHWmA3JjeEhbKB8LHa6c7UWLAfLOYuG/Toc1w/g8jQIi0nB6kGFkx
EjdYHFtYoNjnmNFQ4fyuZDzwc2efHMTl/rbTwWMLK70sekScLHFI0drROzBGifmo
ScsYX+PY4iA=
-----END CERTIFICATE-----
"""
TEST_TLS_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEoQIBAAKCAQEAqmsxl4ovgLFXNoQDt4+ZSZycM2HXdEVhuku3p16mgTZOCrUF
g5FEMV+tHXD4mzUwZkVyjUHWN5P9f0v4tG/t6Zs7uGiPXxRQH3Lrw4XZ7XEtWwLt
lg4Gx8qGSBGl8jC8HSIgr80xky4k6GMPR1FJHfYgSJLe2wBYBUm/JgA5MBI1UvWS
pMR9yXuXejBdWrHZCtNQzclCtfMeq+NTCrz+7T1j+8uz2LxCl91Lf0vEyuZLb/iE
cL56sm18GWF8zrwoQDto1oQAhhhd5Hne5T2S9yAYarLWK/ZK+UBKGB1soxW4nM4g
MN+IfSpqdD85dZyS4BHEyXbzd7M47cqFLIUeeQIDAQABAoH/XpxkS91LwgayhHGG
HsJ6N4PatCv9kW9zchnXO/QwPEwJx6f4B7L+SOr1EQNHAePlmuGzVvjWFMT0V1e2
G3aIfsjPvvFNp1t/n/YNLd+BvXC33W8it8vRt9mX8yrZFjw4M3Re8TrZ6vwTQXC9
arqV/SxHgAMJ9kuaklT+6fn1xdlshonPClFs4QNTDt/pEdl+rZiyebk4AlrPbgkO
/f1j8JHBItLcZMxv25PwZQGdt1+eEB2ck+PaInwV2yRpqUVSjWzX2pC/2bfXVNqB
ElHfv1y5mVKqm6wZssI3n6GkMu1GKj8loRa4icjnWXRVo4yfMzF+Bpb8kycRsjhV
NVlRAoGBAOqIh5d75RzQ/nRvkm9OZPL8SjA+aCaim8v0BT0u3yp4WUnr+4w39xK7
VmSVaGF/hIi6mNYvBMhExXiwzEOYIAFE+ShGivROMOj4XxmYKNJugFxsvvMwRLBF
30WHziAVanQmo7O8gCyBW0IL8VVA3L6WOVAIMcvVYZXHrkkU6AuRAoGBALoEXLey
cDb2WTI5UMI+RDn9Qm2og6iaTRDpOqUB1JXD/hhXRXcqgJhGxdxhixu+re/ajfkc
HySJa0aQ7P/tjf849Il1y2F6Yguvo0jtCD3SuXOP/cQJRLRgU29/sh1Z/F8W42Ny
Xy3S0jB/Ez7GaHM/mJ3J5z5NoqiZiy7v4mBpAoGAV3ysj86QrcosUUTZbBnjQFzq
U8rD0T2xPkh9t9AHQXF5ZUDZKfoqeVtWo9i0AkKuLs7kemk5sHcu7pGM8N4Lek2/
X83Iwc91IUKdPw/qkmzUByYtqMvlo5e87NP3CTLT7hYH1OFJMtDiOOX5lWLHtXSW
Votn//BOIbBGDE73LHECgYEAkIDZAf8FOz0uV1y9BthWKfI7C3LQLEcJvSxhWVPN
sDZcCs6o8QS8dw7rn+LKrNf4yQ4wIiedbcWu51eoNLx3BaBaHvq57tSim89qejlg
oJ41YLen/ATzMWhvCHvbgv+nlLr0FAoCNFfE3tWovqhk9bqetVzmXbjztiPpQqIp
apkCgYAkpt2DLGZsYANjJkHPmS/XoeMu2/HXjS0XMJ7LkxFAL1lr4jjuLM5EDo7S
5YNpp/gqyOQ00ygD6OniD/lXYdB/nKHpPwVyd+HpxCBKTp7azgywu8dmYGPTa6IE
Zcfss6uRVVB/g03ZX5HUdqSGg505DLddjt24+Rkog6BwM138zg==
-----END RSA PRIVATE KEY-----
"""


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


def _json_get(path, scheme="http"):
    """GET a JSON endpoint on localhost:TEST_PORT."""
    req = Request(f"{scheme}://127.0.0.1:{TEST_PORT}{path}")
    kwargs = {"timeout": 5}
    if scheme == "https":
        kwargs["context"] = TLS_CONTEXT
    with urlopen(req, **kwargs) as resp:
        return json.loads(resp.read()), resp.status


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


def _head(path, scheme="http"):
    """HEAD request to localhost:TEST_PORT."""
    req = Request(f"{scheme}://127.0.0.1:{TEST_PORT}{path}", method="HEAD")
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
            data, status = _json_get("/api/networks", scheme="https")
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
                "/api/wifi",
                {"ssid": "NewNet", "password": "pass123"},
                scheme="https",
            )
            _assert_has_fields(data, {"message"})
        finally:
            server.stop()

    @patch("camera_streamer.status_server.wifi.connect_network")
    def test_error_missing_ssid(self, mock_connect, noauth_config):
        server = CameraStatusServer(noauth_config)
        server.start()
        try:
            data, status = _json_post(
                "/api/wifi",
                {"ssid": "", "password": "pass"},
                scheme="https",
            )
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
                scheme="https",
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

    def test_requires_auth(self, noauth_config):
        """Without password, /api/stream-config works without auth."""
        server = CameraStatusServer(
            noauth_config,
            control_handler=_make_control_handler(noauth_config),
        )
        server.start()
        try:
            data, status = _json_put(
                "/api/stream-config",
                {"fps": 20},
                scheme="https",
            )
            _assert_has_fields(data, {"applied", "status"})
        finally:
            server.stop()

    def test_error_on_invalid_param(self, noauth_config):
        """Invalid param returns {error}."""
        server = CameraStatusServer(
            noauth_config,
            control_handler=_make_control_handler(noauth_config),
        )
        server.start()
        try:
            data, status = _json_put(
                "/api/stream-config",
                {"unknown_field": 42},
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
