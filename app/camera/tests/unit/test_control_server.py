# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Unit tests for the dedicated camera control listener."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from camera_streamer.control_server import (
    CONTROL_API_PREFIX,
    CONTROL_ROUTE_MATRIX,
    CameraControlServer,
    _wrap_https_server,
)


@pytest.fixture
def tls_config(tmp_path):
    certs_dir = tmp_path / "certs"
    certs_dir.mkdir()
    cfg = MagicMock()
    cfg.certs_dir = str(certs_dir)
    return cfg


class TestControlTls:
    @patch("camera_streamer.control_server.ssl.SSLContext")
    @patch("camera_streamer.control_server.status_server_module._ensure_tls_material")
    def test_wrap_https_server_requires_mtls(
        self, mock_ensure_tls_material, mock_ssl_context, tls_config
    ):
        ca_path = Path(tls_config.certs_dir) / "ca.crt"
        ca_path.write_text("CA")
        mock_ensure_tls_material.return_value = ("server.crt", "server.key")
        server = MagicMock()
        original_socket = server.socket
        ctx = MagicMock()
        mock_ssl_context.return_value = ctx

        wrapped = _wrap_https_server(server, tls_config)

        assert wrapped is server
        ctx.load_cert_chain.assert_called_once_with("server.crt", "server.key")
        ctx.load_verify_locations.assert_called_once_with(str(ca_path))
        assert ctx.verify_mode.name == "CERT_REQUIRED"
        ctx.wrap_socket.assert_called_once_with(original_socket, server_side=True)

    def test_start_skips_when_pairing_ca_missing(self, tls_config):
        server = CameraControlServer(tls_config, control_handler=MagicMock())
        assert server.start() is False

    def test_control_routes_stay_under_control_prefix(self):
        for routes in CONTROL_ROUTE_MATRIX.values():
            assert all(path.startswith(CONTROL_API_PREFIX) for path in routes)
