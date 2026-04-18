"""Tests for OTAAgent — server→camera push endpoint.

The agent delegates all install work to the root-privileged
`camera-ota-installer.service` via ota_installer's trigger-file
protocol, so these tests focus on the HTTP surface and the
agent's interaction with the installer module.
"""

import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from camera_streamer import ota_installer
from camera_streamer.ota_agent import MAX_BUNDLE_SIZE, OTA_PORT, OTAAgent


@pytest.fixture
def config(tmp_path):
    cfg = MagicMock()
    cfg.data_dir = str(tmp_path)
    cfg.certs_dir = str(tmp_path / "certs")
    os.makedirs(cfg.certs_dir, exist_ok=True)
    return cfg


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """Redirect the installer spool to a tmp dir so tests don't touch
    /var/lib/camera-ota."""
    spool_dir = tmp_path / "spool"
    staging = spool_dir / "staging"
    staging.mkdir(parents=True)
    monkeypatch.setattr(ota_installer, "SPOOL_DIR", str(spool_dir))
    monkeypatch.setattr(ota_installer, "STAGING_DIR", str(staging))
    monkeypatch.setattr(
        ota_installer, "TRIGGER_PATH", str(spool_dir / "trigger")
    )
    monkeypatch.setattr(
        ota_installer, "STATUS_PATH", str(spool_dir / "status.json")
    )
    return spool_dir


@pytest.fixture
def agent(config, spool):
    return OTAAgent(config)


def _make_handler(body, content_length=None):
    handler = MagicMock()
    handler.headers = {"Content-Length": str(content_length or len(body))}
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    return handler


class TestStatusProxy:
    def test_defaults_to_idle(self, agent):
        assert agent.status["state"] == "idle"
        assert agent.status["progress"] == 0

    def test_reads_installer_status(self, agent, spool):
        ota_installer.write_status("installing", progress=42)
        assert agent.status["state"] == "installing"
        assert agent.status["progress"] == 42


class TestStartStop:
    def test_start_creates_thread(self, agent):
        with patch.object(agent, "_run_server"):
            agent.start()
            assert agent._thread is not None
            assert agent._running is True
            agent.stop()

    def test_stop_clears_flag(self, agent):
        with patch.object(agent, "_run_server"):
            agent.start()
            agent.stop()
            assert agent._running is False

    def test_start_idempotent(self, agent):
        with patch.object(agent, "_run_server"):
            agent.start()
            thread1 = agent._thread
            agent.start()
            assert agent._thread is thread1
            agent.stop()


class TestWrapTLS:
    def test_no_certs_returns_plain(self, agent):
        mock_server = MagicMock()
        assert agent._wrap_tls(mock_server) is mock_server

    def test_with_certs_wraps_socket(self, agent, config):
        for name in ["client.crt", "client.key", "ca.crt"]:
            with open(os.path.join(config.certs_dir, name), "w") as f:
                f.write("FAKE")
        mock_server = MagicMock()
        with patch("camera_streamer.ota_agent.ssl.SSLContext") as mock_ctx:
            mock_ctx.return_value = MagicMock()
            agent._wrap_tls(mock_server)
            mock_ctx.assert_called_once()


class TestHandleUpload:
    def test_rejects_no_content(self, agent):
        handler = _make_handler(b"", content_length=0)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(400)

    def test_rejects_oversized(self, agent):
        handler = _make_handler(b"x", content_length=MAX_BUNDLE_SIZE + 1)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(400)

    def test_rejects_when_busy(self, agent, spool):
        ota_installer.write_status("installing", progress=50)
        # Write a trigger so is_busy() returns True.
        open(ota_installer.TRIGGER_PATH, "w").close()
        handler = _make_handler(b"some bytes")
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(409)

    def test_rejects_incomplete_upload(self, agent):
        handler = _make_handler(b"short", content_length=1000)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(500)

    @patch("camera_streamer.ota_agent.ota_installer.wait_for_completion")
    def test_successful_install(self, mock_wait, agent, spool):
        mock_wait.return_value = {
            "state": "installed",
            "progress": 100,
            "error": "",
        }
        body = b"fake-swu-content" * 500
        handler = _make_handler(body)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(200)
        # Bundle staged to the spool, trigger written.
        assert os.path.isfile(os.path.join(spool, "staging", "update.swu"))
        # NOTE: trigger_install is real; the trigger file was written
        # and wait_for_completion is the only thing mocked.
        assert os.path.isfile(ota_installer.TRIGGER_PATH)
        mock_wait.assert_called_once()

    @patch("camera_streamer.ota_agent.ota_installer.wait_for_completion")
    def test_install_error_returns_500(self, mock_wait, agent, spool):
        mock_wait.return_value = {
            "state": "error",
            "progress": 30,
            "error": "Signature verification failed",
        }
        body = b"fake-swu-content"
        handler = _make_handler(body)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(500)


class TestHandleStatus:
    def test_returns_status_json(self, agent, spool):
        ota_installer.write_status("installing", progress=77)
        handler = MagicMock()
        handler.wfile = io.BytesIO()
        agent._handle_status(handler)
        handler.send_response.assert_called_with(200)
        data = json.loads(handler.wfile.getvalue())
        assert data["state"] == "installing"
        assert data["progress"] == 77


class TestConstants:
    def test_port(self):
        assert OTA_PORT == 8080

    def test_max_bundle_size(self):
        assert MAX_BUNDLE_SIZE == 500 * 1024 * 1024
