# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Integration tests for the camera human/control listener split."""

import shutil
import ssl
import subprocess
from http.client import RemoteDisconnected
from pathlib import Path
from shutil import copyfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from camera_streamer.control import ControlHandler
from camera_streamer.control_server import CameraControlServer
from camera_streamer.sensor_info import capabilities_for_testing
from camera_streamer.status_server import CameraStatusServer

HUMAN_PORT = 18443
CONTROL_PORT = 18444


def _ssl_context(cert_path="", key_path=""):
    ctx = ssl._create_unverified_context()
    if cert_path and key_path:
        ctx.load_cert_chain(cert_path, key_path)
    return ctx


def _write_test_mtls_material(tmp_path: Path) -> dict[str, str]:
    openssl = shutil.which("openssl")
    if openssl is None:
        for candidate in (
            r"C:\Program Files\Git\usr\bin\openssl.exe",
            r"C:\Program Files\Git\mingw64\bin\openssl.exe",
        ):
            if Path(candidate).is_file():
                openssl = candidate
                break
    if openssl is None:
        pytest.skip("openssl CLI unavailable")

    ca_key = tmp_path / "ca.key"
    ca_crt = tmp_path / "ca.crt"
    client_key = tmp_path / "client.key"
    client_csr = tmp_path / "client.csr"
    client_crt = tmp_path / "client.crt"
    server_key = tmp_path / "server.key"
    server_crt = tmp_path / "server.crt"

    commands = [
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_crt),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=Camera Test CA",
        ],
        [
            openssl,
            "req",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(client_key),
            "-out",
            str(client_csr),
            "-nodes",
            "-subj",
            "/CN=paired-server",
        ],
        [
            openssl,
            "x509",
            "-req",
            "-in",
            str(client_csr),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(client_crt),
            "-days",
            "1",
            "-sha256",
        ],
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(server_key),
            "-out",
            str(server_crt),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
    ]

    for command in commands:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )

    return {
        "ca_crt": str(ca_crt),
        "client_crt": str(client_crt),
        "client_key": str(client_key),
        "server_crt": str(server_crt),
        "server_key": str(server_key),
    }


@pytest.fixture
def split_listeners(camera_config, monkeypatch, tmp_path):
    material = _write_test_mtls_material(tmp_path)
    copyfile(material["ca_crt"], Path(camera_config.certs_dir) / "ca.crt")

    monkeypatch.setattr("camera_streamer.status_server.LISTEN_PORT", HUMAN_PORT)
    monkeypatch.setattr(
        "camera_streamer.control_server.CONTROL_LISTEN_PORT", CONTROL_PORT
    )
    monkeypatch.setattr(
        "camera_streamer.status_server._ensure_tls_material",
        lambda _config: (material["server_crt"], material["server_key"]),
    )

    control_handler = ControlHandler(
        camera_config,
        None,
        sensor_capabilities=capabilities_for_testing("ov5647"),
    )
    status_server = CameraStatusServer(
        camera_config,
        control_handler=control_handler,
    )
    control_server = CameraControlServer(
        camera_config,
        control_handler=control_handler,
    )

    assert status_server.start() is True
    assert control_server.start() is True

    try:
        yield {
            "client_crt": material["client_crt"],
            "client_key": material["client_key"],
        }
    finally:
        control_server.stop()
        status_server.stop()


class TestListenerSeparation:
    def test_control_path_on_human_listener_returns_404_without_client_cert(
        self, split_listeners
    ):
        request = Request(f"https://127.0.0.1:{HUMAN_PORT}/api/v1/control/config")

        with pytest.raises(HTTPError) as excinfo:
            urlopen(request, context=_ssl_context(), timeout=5)

        assert excinfo.value.code == 404

    def test_control_path_on_human_listener_returns_404_with_client_cert(
        self, split_listeners
    ):
        request = Request(f"https://127.0.0.1:{HUMAN_PORT}/api/v1/control/config")

        with pytest.raises(HTTPError) as excinfo:
            urlopen(
                request,
                context=_ssl_context(
                    split_listeners["client_crt"], split_listeners["client_key"]
                ),
                timeout=5,
            )

        assert excinfo.value.code == 404

    def test_human_path_on_control_listener_requires_mtls(self, split_listeners):
        request = Request(f"https://127.0.0.1:{CONTROL_PORT}/login")

        with pytest.raises((RemoteDisconnected, URLError, ssl.SSLError)) as excinfo:
            urlopen(request, context=_ssl_context(), timeout=5)

        assert (
            "certificate" in str(excinfo.value).lower()
            or "closed connection" in str(excinfo.value).lower()
            or "ssl" in str(excinfo.value).lower()
        )

    def test_human_path_on_control_listener_returns_404_with_client_cert(
        self, split_listeners
    ):
        request = Request(f"https://127.0.0.1:{CONTROL_PORT}/login")

        with pytest.raises(HTTPError) as excinfo:
            urlopen(
                request,
                context=_ssl_context(
                    split_listeners["client_crt"], split_listeners["client_key"]
                ),
                timeout=5,
            )

        assert excinfo.value.code == 404
