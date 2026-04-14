"""Tests for OTAAgent — camera-side OTA update handler."""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from camera_streamer.ota_agent import CHUNK_SIZE, MAX_BUNDLE_SIZE, OTA_PORT, OTAAgent


@pytest.fixture
def config(tmp_path):
    """Create a mock config with temp directories."""
    cfg = MagicMock()
    cfg.data_dir = str(tmp_path)
    cfg.certs_dir = str(tmp_path / "certs")
    os.makedirs(cfg.certs_dir, exist_ok=True)
    return cfg


@pytest.fixture
def agent(config):
    """Create an OTAAgent with mock config."""
    return OTAAgent(config)


class TestInit:
    """Test agent initialization."""

    def test_default_status_idle(self, agent):
        assert agent.status["state"] == "idle"
        assert agent.status["progress"] == 0
        assert agent.status["error"] == ""

    def test_staging_dir(self, agent, config):
        expected = os.path.join(config.data_dir, "ota", "staging")
        assert agent.staging_dir == expected

    def test_status_returns_copy(self, agent):
        s1 = agent.status
        s1["state"] = "modified"
        assert agent.status["state"] == "idle"


class TestSetStatus:
    """Test status updates."""

    def test_set_status(self, agent):
        agent._set_status("downloading", progress=25)
        assert agent.status["state"] == "downloading"
        assert agent.status["progress"] == 25

    def test_set_status_with_error(self, agent):
        agent._set_status("error", error="disk full")
        assert agent.status["state"] == "error"
        assert agent.status["error"] == "disk full"

    def test_set_status_preserves_fields(self, agent):
        agent._set_status("downloading", progress=50)
        agent._set_status("verifying")
        assert agent.status["progress"] == 50  # preserved


class TestVerifyBundle:
    """Test bundle signature verification."""

    def test_no_public_key_skips(self, agent, tmp_path):
        """Should skip verification when no public key exists (dev mode)."""
        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        valid, err = agent._verify_bundle(bundle)
        assert valid is True
        assert err == ""

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_verify_success(self, mock_run, agent, config, tmp_path):
        """Should return True when swupdate verification passes."""
        # Create verification cert so verification runs
        key_path = os.path.join(config.certs_dir, "swupdate-public.crt")
        with open(key_path, "w") as f:
            f.write("PUBLIC KEY")

        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        valid, err = agent._verify_bundle(bundle)
        assert valid is True

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_verify_failure(self, mock_run, agent, config, tmp_path):
        """Should return False when signature is invalid."""
        key_path = os.path.join(config.certs_dir, "swupdate-public.crt")
        with open(key_path, "w") as f:
            f.write("PUBLIC KEY")

        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="bad signature"
        )
        valid, err = agent._verify_bundle(bundle)
        assert valid is False
        assert "bad signature" in err

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_swupdate_not_found(self, mock_run, agent, config, tmp_path):
        """Should skip verification when swupdate not installed."""
        key_path = os.path.join(config.certs_dir, "swupdate-public.crt")
        with open(key_path, "w") as f:
            f.write("PUBLIC KEY")

        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.side_effect = FileNotFoundError
        valid, err = agent._verify_bundle(bundle)
        assert valid is True  # dev mode fallback

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_verify_timeout(self, mock_run, agent, config, tmp_path):
        key_path = os.path.join(config.certs_dir, "swupdate-public.crt")
        with open(key_path, "w") as f:
            f.write("PUBLIC KEY")

        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.side_effect = subprocess.TimeoutExpired("swupdate", 60)
        valid, err = agent._verify_bundle(bundle)
        assert valid is False
        assert "timed out" in err.lower()


class TestInstallBundle:
    """Test bundle installation via swupdate."""

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_install_success(self, mock_run, agent, config, tmp_path):
        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key_path = os.path.join(config.certs_dir, "swupdate-public.crt")
        with open(key_path, "w") as f:
            f.write("PUBLIC KEY")

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, err = agent._install_bundle(bundle)
        assert ok is True
        assert err == ""
        assert mock_run.call_args[0][0] == [
            "swupdate",
            "-i",
            bundle,
            "-k",
            key_path,
        ]

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_install_failure(self, mock_run, agent, config, tmp_path):
        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")
        key_path = os.path.join(config.certs_dir, "swupdate-public.crt")
        with open(key_path, "w") as f:
            f.write("PUBLIC KEY")

        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="write failed"
        )
        ok, err = agent._install_bundle(bundle)
        assert ok is False
        assert "write failed" in err
        assert mock_run.call_args[0][0] == [
            "swupdate",
            "-i",
            bundle,
            "-k",
            key_path,
        ]

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_install_without_key_uses_plain_command(self, mock_run, agent, tmp_path):
        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, err = agent._install_bundle(bundle)
        assert ok is True
        assert err == ""
        assert mock_run.call_args[0][0] == ["swupdate", "-i", bundle]

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_install_not_found(self, mock_run, agent, tmp_path):
        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.side_effect = FileNotFoundError
        ok, err = agent._install_bundle(bundle)
        assert ok is False
        assert "not installed" in err

    @patch("camera_streamer.ota_agent.subprocess.run")
    def test_install_timeout(self, mock_run, agent, tmp_path):
        bundle = str(tmp_path / "test.swu")
        with open(bundle, "wb") as f:
            f.write(b"test")

        mock_run.side_effect = subprocess.TimeoutExpired("swupdate", 600)
        ok, err = agent._install_bundle(bundle)
        assert ok is False
        assert "timed out" in err.lower()


class TestCleanup:
    """Test bundle cleanup."""

    def test_removes_file(self, agent, tmp_path):
        path = str(tmp_path / "test.swu")
        with open(path, "w") as f:
            f.write("test")
        agent._cleanup(path)
        assert not os.path.exists(path)

    def test_handles_missing_file(self, agent):
        agent._cleanup("/nonexistent/file.swu")  # Should not raise


class TestStartStop:
    """Test agent lifecycle."""

    def test_start_creates_thread(self, agent):
        with patch.object(agent, "_run_server"):
            agent.start()
            assert agent._thread is not None
            assert agent._running is True
            agent.stop()

    def test_stop_sets_flag(self, agent):
        with patch.object(agent, "_run_server"):
            agent.start()
            agent.stop()
            assert agent._running is False

    def test_start_idempotent(self, agent):
        with patch.object(agent, "_run_server"):
            agent.start()
            thread1 = agent._thread
            agent.start()  # Should not create a second thread
            assert agent._thread is thread1
            agent.stop()


class TestWrapTLS:
    """Test mTLS wrapper."""

    def test_no_certs_returns_plain_server(self, agent):
        """Should return server unchanged when no certs exist."""
        mock_server = MagicMock()
        result = agent._wrap_tls(mock_server)
        assert result is mock_server

    def test_with_certs_wraps_socket(self, agent, config):
        """Should attempt to wrap socket when all certs exist."""
        for name in ["client.crt", "client.key", "ca.crt"]:
            with open(os.path.join(config.certs_dir, name), "w") as f:
                f.write("FAKE")

        mock_server = MagicMock()
        with patch("camera_streamer.ota_agent.ssl.SSLContext") as mock_ctx:
            mock_ctx.return_value = MagicMock()
            agent._wrap_tls(mock_server)
            mock_ctx.assert_called_once()


class TestHandleUpload:
    """Test the upload handler logic."""

    def _make_handler(self, body, content_length=None):
        """Create a mock HTTP handler with given body."""
        import io

        handler = MagicMock()
        handler.headers = {"Content-Length": str(content_length or len(body))}
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        return handler

    def test_rejects_no_content(self, agent):
        handler = self._make_handler(b"", content_length=0)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(400)

    def test_rejects_oversized(self, agent):
        handler = self._make_handler(b"x", content_length=MAX_BUNDLE_SIZE + 1)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(400)

    @patch.object(OTAAgent, "_install_bundle", return_value=(True, ""))
    @patch.object(OTAAgent, "_verify_bundle", return_value=(True, ""))
    def test_successful_upload(self, mock_verify, mock_install, agent, config):
        """Should download, verify, install, and return 200."""
        body = b"fake-swu-content" * 100
        handler = self._make_handler(body)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(200)
        mock_verify.assert_called_once()
        mock_install.assert_called_once()
        assert agent.status["state"] == "installed"

    @patch.object(OTAAgent, "_verify_bundle", return_value=(False, "bad sig"))
    def test_verification_failure(self, mock_verify, agent, config):
        """Should return 400 on verification failure."""
        body = b"fake-swu-content"
        handler = self._make_handler(body)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(400)
        assert agent.status["state"] == "error"

    @patch.object(OTAAgent, "_install_bundle", return_value=(False, "disk full"))
    @patch.object(OTAAgent, "_verify_bundle", return_value=(True, ""))
    def test_install_failure(self, mock_verify, mock_install, agent, config):
        """Should return 500 on install failure."""
        body = b"fake-swu-content"
        handler = self._make_handler(body)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(500)
        assert agent.status["state"] == "error"

    @patch.object(OTAAgent, "_install_bundle", return_value=(True, ""))
    @patch.object(OTAAgent, "_verify_bundle", return_value=(True, ""))
    def test_streams_to_disk(self, mock_verify, mock_install, agent, config):
        """Should write bundle to staging dir, not hold in memory."""
        body = b"x" * (CHUNK_SIZE * 3)  # Multiple chunks
        handler = self._make_handler(body)
        agent._handle_upload(handler)
        # Verify the bundle was passed to verify and install
        bundle_path = mock_verify.call_args[0][0]
        assert "staging" in bundle_path
        assert bundle_path.endswith("update.swu")

    def test_incomplete_upload(self, agent, config):
        """Should reject incomplete uploads."""
        body = b"short"
        handler = self._make_handler(body, content_length=1000)
        agent._handle_upload(handler)
        handler.send_response.assert_called_with(400)
        assert agent.status["state"] == "error"


class TestHandleStatus:
    """Test the status handler."""

    def test_returns_status_json(self, agent):
        import io
        import json

        handler = MagicMock()
        handler.wfile = io.BytesIO()
        agent._set_status("installing", progress=75)
        agent._handle_status(handler)
        handler.send_response.assert_called_with(200)
        # Parse the response body
        body = handler.wfile.getvalue()
        data = json.loads(body)
        assert data["state"] == "installing"
        assert data["progress"] == 75


class TestConstants:
    """Test module constants."""

    def test_port(self):
        assert OTA_PORT == 8080

    def test_max_bundle_size(self):
        assert MAX_BUNDLE_SIZE == 500 * 1024 * 1024

    def test_chunk_size(self):
        assert CHUNK_SIZE == 64 * 1024
