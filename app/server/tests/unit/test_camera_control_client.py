# REQ: SWR-039, SWR-065, SWR-066; RISK: RISK-007, RISK-015; SEC: SC-002, SC-012; TEST: TC-037, TC-054
"""Unit tests for CameraControlClient (ADR-0015)."""

from __future__ import annotations

import ast
import json
import ssl
import threading
import types
from http import client as _http_client
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.camera_control_client import (
    CERT_MISMATCH_ERROR,
    CONTROL_PORT,
    CameraControlClient,
)
from monitor.services.camera_trust import (
    persist_pinned_status_cert,
    status_cert_fingerprint_from_pem,
)


def _tls_fixture_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "tls"


def _read_tls_fixture(name: str) -> str:
    return (_tls_fixture_dir() / name).read_text(encoding="utf-8")


def _write_tls_pair(tmp_path: Path, prefix: str) -> tuple[str, str]:
    cert_path = tmp_path / f"{prefix}.crt"
    key_path = tmp_path / f"{prefix}.key"
    cert_path.write_text(_read_tls_fixture(f"{prefix}.crt"), encoding="utf-8")
    key_path.write_text(_read_tls_fixture(f"{prefix}.key"), encoding="utf-8")
    return str(cert_path), str(key_path)


class _JsonHandler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "method": self.command,
                "path": self.path,
                "body": body,
            }
        )
        payload = {"ok": True}
        if body:
            payload["received"] = json.loads(body.decode("utf-8"))
        if self.path.endswith("/stream/start"):
            payload = {"state": "running"}
        elif self.path.endswith("/stream/stop"):
            payload = {"state": "stopped"}
        elif self.path.endswith("/stream/state"):
            payload = {"state": "running"}
        elif self.path.endswith("/control/status"):
            payload = {"status": "ok"}
        response = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def log_message(self, format, *args) -> None:
        return


@pytest.fixture
def client_factory(data_dir):
    certs_dir = data_dir / "certs"

    def _make():
        pins: dict[str, str] = {}
        audit = MagicMock()
        client = CameraControlClient(
            str(certs_dir),
            pin_provider=lambda camera_id: pins.get(camera_id, ""),
            pin_recorder=lambda camera_id, fingerprint: pins.__setitem__(
                camera_id, fingerprint
            ),
            audit=audit,
        )
        return client, pins, audit, certs_dir

    return _make


@pytest.fixture
def client(client_factory):
    instance, _pins, _audit, _certs_dir = client_factory()
    return instance


@pytest.fixture
def https_server(tmp_path):
    servers: list[tuple[HTTPServer, threading.Thread]] = []

    def _start(prefix: str):
        cert_path, key_path = _write_tls_pair(tmp_path, prefix)
        server = HTTPServer(("127.0.0.1", 0), _JsonHandler)
        server.requests = []  # type: ignore[attr-defined]

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server

    yield _start

    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _patched_https_connection(port: int):
    real_conn = _http_client.HTTPSConnection

    def _factory(host, requested_port=None, *, context=None, timeout=None):
        return real_conn("127.0.0.1", port=port, context=context, timeout=timeout)

    fake_http_client = types.SimpleNamespace(
        HTTPSConnection=_factory,
        HTTPException=_http_client.HTTPException,
    )

    return patch(
        "monitor.services.camera_control_client.http_client",
        new=fake_http_client,
    )


class TestCameraControlClientUnit:
    """Unit tests that don't require a running camera server."""

    def test_init_stores_certs_dir(self, client, data_dir):
        assert client._certs_dir == str(data_dir / "certs")
        assert client._control_port == CONTROL_PORT

    def test_init_allows_custom_control_port(self, data_dir):
        certs_dir = data_dir / "certs"
        custom = CameraControlClient(str(certs_dir), control_port=9443)
        assert custom._control_port == 9443

    def test_rejects_missing_camera_id(self, client):
        result, err = client.get_config("192.168.99.99")
        assert result is None
        assert "Camera ID required" in err

    def test_get_config_unreachable(self, client):
        result, err = client.get_config("192.168.99.99", camera_id="cam-001")
        assert result is None
        assert err

    def test_connection_refused_returns_firmware_mismatch_error(self, client):
        with patch.object(
            client,
            "_bootstrap_request",
            side_effect=ConnectionRefusedError(111, "Connection refused"),
        ):
            result, err = client.get_config("10.0.0.1", camera_id="cam-001")

        assert result is None
        assert err == "camera control port unreachable (firmware mismatch?)"

    def test_verified_request_uses_custom_control_port(self, data_dir):
        certs_dir = data_dir / "certs"
        client = CameraControlClient(str(certs_dir), control_port=9443)
        calls: dict[str, object] = {}

        class _Response:
            status = 200

            def read(self):
                return b"{}"

        class _Connection:
            def __init__(self, host, port=None, *, context=None, timeout=None):
                calls["host"] = host
                calls["port"] = port
                calls["context"] = context
                calls["timeout"] = timeout

            def request(self, method, path, body=None, headers=None):
                calls["method"] = method
                calls["path"] = path

            def getresponse(self):
                return _Response()

            def close(self):
                calls["closed"] = True

        fake_http_client = types.SimpleNamespace(
            HTTPSConnection=_Connection,
            HTTPException=_http_client.HTTPException,
        )
        with (
            patch(
                "monitor.services.camera_control_client.http_client", fake_http_client
            ),
            patch.object(client, "_ssl_context", return_value=object()),
        ):
            result, err = client._verified_request(
                "GET",
                "cam-001",
                "camera.local",
                "/api/v1/control/config",
                None,
            )

        assert err == ""
        assert result == {}
        assert calls["host"] == "camera.local"
        assert calls["port"] == 9443
        assert calls["path"] == "/api/v1/control/config"
        assert calls["closed"] is True

    def test_ssl_context_requires_verification_for_pinned_camera(self, client_factory):
        client, pins, _audit, certs_dir = client_factory()
        pinned_cert = _read_tls_fixture("camera-valid.crt")
        pins["cam-001"] = status_cert_fingerprint_from_pem(pinned_cert)
        persist_pinned_status_cert(str(certs_dir), "cam-001", pinned_cert)

        ctx = client._ssl_context("cam-001")

        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is False

    def test_non_test_cert_none_usage_is_explicitly_scoped(self):
        repo_root = Path(__file__).resolve().parents[4]
        service_root = repo_root / "app" / "server" / "monitor" / "services"
        allowed = {
            (
                "app/server/monitor/services/camera_control_client.py",
                "_bootstrap_request",
            ),
            ("app/server/monitor/services/camera_ota_client.py", "_ssl_context"),
        }
        found: set[tuple[str, str]] = set()

        for path in service_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node

            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Attribute)
                    and node.attr == "CERT_NONE"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "ssl"
                ):
                    continue

                function_name = ""
                parent = node
                while parent in parents:
                    parent = parents[parent]
                    if isinstance(parent, ast.FunctionDef):
                        function_name = parent.name
                        break
                relative = path.relative_to(repo_root).as_posix()
                found.add((relative, function_name))

        assert found == allowed


class TestPinnedControlTls:
    def test_set_config_succeeds_with_pinned_cert(self, client_factory, https_server):
        client, pins, _audit, certs_dir = client_factory()
        pinned_cert = _read_tls_fixture("camera-valid.crt")
        pins["cam-001"] = status_cert_fingerprint_from_pem(pinned_cert)
        persist_pinned_status_cert(str(certs_dir), "cam-001", pinned_cert)
        server = https_server("camera-valid")

        with _patched_https_connection(server.server_address[1]):
            result, err = client.set_config(
                "camera.local",
                {"fps": 15},
                camera_id="cam-001",
            )

        assert err == ""
        assert result["received"]["fps"] == 15
        assert len(server.requests) == 1  # type: ignore[attr-defined]

    def test_set_config_rejects_mismatched_cert_before_http(
        self, client_factory, https_server
    ):
        client, pins, _audit, certs_dir = client_factory()
        pinned_cert = _read_tls_fixture("camera-valid.crt")
        pins["cam-001"] = status_cert_fingerprint_from_pem(pinned_cert)
        persist_pinned_status_cert(str(certs_dir), "cam-001", pinned_cert)
        server = https_server("camera-mismatch")

        with _patched_https_connection(server.server_address[1]):
            result, err = client.set_config(
                "camera.local",
                {"fps": 15},
                camera_id="cam-001",
            )

        assert result is None
        assert err == CERT_MISMATCH_ERROR
        assert len(server.requests) == 0  # type: ignore[attr-defined]

    def test_legacy_camera_tofu_pins_once(self, client_factory, https_server):
        client, pins, audit, certs_dir = client_factory()
        server = https_server("camera-valid")

        with _patched_https_connection(server.server_address[1]):
            first_result, first_err = client.get_status(
                "camera.local",
                camera_id="cam-001",
            )
            second_result, second_err = client.get_status(
                "camera.local",
                camera_id="cam-001",
            )

        assert first_err == ""
        assert second_err == ""
        assert first_result["status"] == "ok"
        assert second_result["status"] == "ok"
        assert pins["cam-001"] == status_cert_fingerprint_from_pem(
            _read_tls_fixture("camera-valid.crt")
        )
        assert (certs_dir / "status" / "cam-001.crt").exists()
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args.kwargs["detail"].startswith(
            "camera cam-001 fingerprint "
        )
        assert len(server.requests) == 2  # type: ignore[attr-defined]


class TestStreamControlEndpoints:
    """ADR-0017: start/stop/state use the correct camera paths."""

    def test_start_stream_uses_correct_path(self, client):
        with patch.object(
            client, "_request", return_value=({"state": "running"}, "")
        ) as mock_request:
            result, err = client.start_stream("10.0.0.1", camera_id="cam-001")
        assert err == ""
        assert result == {"state": "running"}
        mock_request.assert_called_once_with(
            "POST",
            "10.0.0.1",
            "/api/v1/control/stream/start",
            {},
            camera_id="cam-001",
        )

    def test_stop_stream_uses_correct_path(self, client):
        with patch.object(
            client, "_request", return_value=({"state": "stopped"}, "")
        ) as mock_request:
            result, err = client.stop_stream("10.0.0.1", camera_id="cam-001")
        assert err == ""
        assert result == {"state": "stopped"}
        mock_request.assert_called_once_with(
            "POST",
            "10.0.0.1",
            "/api/v1/control/stream/stop",
            {},
            camera_id="cam-001",
        )

    def test_get_stream_state_uses_correct_path(self, client):
        with patch.object(
            client, "_request", return_value=({"state": "running"}, "")
        ) as mock_request:
            result, err = client.get_stream_state("10.0.0.1", camera_id="cam-001")
        assert err == ""
        assert result == {"state": "running"}
        mock_request.assert_called_once_with(
            "GET",
            "10.0.0.1",
            "/api/v1/control/stream/state",
            camera_id="cam-001",
        )

    def test_start_stream_idempotent_already_running(self, client):
        with patch.object(
            client,
            "_request",
            return_value=({"state": "running"}, ""),
        ):
            result, err = client.start_stream("10.0.0.1", camera_id="cam-001")
        assert err == ""
        assert result["state"] == "running"


class TestCameraServiceWithControl:
    """Test CameraService integration with control client."""

    def test_update_pushes_stream_params(self, app, cameras_json):
        with app.app_context():
            mock_control = MagicMock()
            mock_control.set_config.return_value = ({"applied": {"fps": 15}}, "")
            app.camera_service._control = mock_control

            err, status = app.camera_service.update(
                "cam-abc123",
                {"fps": 15},
                user="admin",
                ip="127.0.0.1",
            )
            assert status == 200
            assert err == ""
            mock_control.set_config.assert_called_once_with(
                "192.168.1.50",
                {"fps": 15},
                camera_id="cam-abc123",
            )

    def test_update_preset_pushes_one_bundle_without_echo_field(
        self, app, cameras_json
    ):
        with app.app_context():
            mock_control = MagicMock()
            mock_control.set_config.return_value = (
                {"applied": {"width": 1920, "height": 1080}},
                "",
            )
            app.camera_service._control = mock_control

            err, status = app.camera_service.update(
                "cam-abc123",
                {
                    "width": 1920,
                    "height": 1080,
                    "fps": 25,
                    "bitrate": 4000000,
                    "h264_profile": "high",
                    "keyframe_interval": 30,
                    "encoder_preset": "balanced",
                },
                user="admin",
                ip="127.0.0.1",
            )
            assert status == 200
            assert err == ""
            mock_control.set_config.assert_called_once_with(
                "192.168.1.50",
                {
                    "width": 1920,
                    "height": 1080,
                    "fps": 25,
                    "bitrate": 4000000,
                    "h264_profile": "high",
                    "keyframe_interval": 30,
                },
                camera_id="cam-abc123",
            )

    def test_update_marks_pending_on_push_failure(self, app, cameras_json):
        with app.app_context():
            mock_control = MagicMock()
            mock_control.set_config.return_value = (None, "Camera unreachable")
            app.camera_service._control = mock_control

            app.camera_service.update(
                "cam-abc123",
                {"fps": 15},
                user="admin",
                ip="127.0.0.1",
            )

            camera = app.store.get_camera("cam-abc123")
            assert camera.config_sync == "pending"

    def test_update_marks_trust_lost_on_cert_mismatch(self, app, cameras_json):
        with app.app_context():
            mock_control = MagicMock()
            mock_control.set_config.return_value = (None, CERT_MISMATCH_ERROR)
            app.camera_service._control = mock_control

            app.camera_service.update(
                "cam-abc123",
                {"fps": 15},
                user="admin",
                ip="127.0.0.1",
            )

            camera = app.store.get_camera("cam-abc123")
            assert camera.config_sync == "trust_lost"

    def test_update_marks_synced_on_push_success(self, app, cameras_json):
        with app.app_context():
            mock_control = MagicMock()
            mock_control.set_config.return_value = ({"applied": {"fps": 15}}, "")
            app.camera_service._control = mock_control

            app.camera_service.update(
                "cam-abc123",
                {"fps": 15},
                user="admin",
                ip="127.0.0.1",
            )

            camera = app.store.get_camera("cam-abc123")
            assert camera.config_sync == "synced"

    def test_update_non_stream_params_no_push(self, app, cameras_json):
        with app.app_context():
            mock_control = MagicMock()
            app.camera_service._control = mock_control

            app.camera_service.update(
                "cam-abc123",
                {"name": "Back Yard"},
                user="admin",
                ip="127.0.0.1",
            )

            mock_control.set_config.assert_not_called()


class TestCameraModelNewFields:
    """Test Camera model has new fields with correct defaults."""

    def test_default_stream_params(self, sample_camera):
        assert sample_camera.width == 1920
        assert sample_camera.height == 1080
        assert sample_camera.bitrate == 4000000
        assert sample_camera.h264_profile == "high"
        assert sample_camera.keyframe_interval == 30
        assert sample_camera.encoder_preset == ""
        assert sample_camera.rotation == 0
        assert sample_camera.hflip is False
        assert sample_camera.vflip is False
        assert sample_camera.config_sync == "unknown"
        assert sample_camera.status_cert_fingerprint == ""

    def test_list_cameras_includes_stream_fields(self, app, cameras_json):
        with app.app_context():
            cameras = app.camera_service.list_cameras()
            camera = cameras[0]
            assert "width" in camera
            assert "height" in camera
            assert "bitrate" in camera
            assert "encoder_preset" in camera
            assert "config_sync" in camera

    def test_camera_status_includes_stream_fields(self, app, cameras_json):
        with app.app_context():
            result, err = app.camera_service.get_camera_status("cam-abc123")
            assert err == ""
            assert "width" in result
            assert "encoder_preset" in result
            assert "config_sync" in result
