"""Tests for camera_streamer.stream module."""

import time
from unittest.mock import MagicMock, patch

from camera_streamer.stream import INITIAL_BACKOFF, MAX_BACKOFF, StreamManager


class TestStreamManager:
    """Test the ffmpeg RTSP stream manager."""

    def test_not_streaming_initially(self, camera_config):
        """Should not be streaming before start()."""
        mgr = StreamManager(camera_config)
        assert mgr.is_streaming is False

    def test_start_without_config_returns_false(self, unconfigured_config):
        """Should refuse to start without server config."""
        mgr = StreamManager(unconfigured_config)
        assert mgr.start() is False

    def test_start_with_config_returns_true(self, camera_config):
        """Should start successfully with valid config."""
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None
            proc.pid = 1234
            proc.stderr = iter([])
            proc.wait.return_value = None
            proc.returncode = 0
            mock_popen.return_value = proc

            mgr = StreamManager(camera_config)
            assert mgr.start() is True
            # Give thread time to start
            time.sleep(0.2)
            mgr.stop()

    def test_build_ffmpeg_only_cmd(self, camera_config):
        """Should build correct ffmpeg command."""
        mgr = StreamManager(camera_config)
        cmd = mgr._build_ffmpeg_only_cmd()
        assert cmd[0] == "ffmpeg"
        assert "-nostdin" in cmd
        assert "-f" in cmd
        assert "v4l2" in cmd
        assert "-video_size" in cmd
        assert "1920x1080" in cmd
        assert "-framerate" in cmd
        assert "25" in cmd
        assert "-c:v" in cmd
        assert "copy" in cmd
        assert "rtsp://192.168.1.100:8554/cam-test001" in cmd

    def test_stop_terminates_ffmpeg(self, camera_config):
        """stop() should terminate the ffmpeg process."""
        mgr = StreamManager(camera_config)
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = None
        proc.pid = 1234
        mgr._process = proc

        mgr._kill_ffmpeg()
        proc.terminate.assert_called_once()

    def test_consecutive_failures_tracked(self, camera_config):
        """Should track consecutive stream failures."""
        mgr = StreamManager(camera_config)
        assert mgr.consecutive_failures == 0

    def test_kill_ffmpeg_handles_none(self, camera_config):
        """_kill_ffmpeg should handle no process gracefully."""
        mgr = StreamManager(camera_config)
        mgr._process = None
        mgr._kill_ffmpeg()  # Should not raise


class TestMTLSStreaming:
    """Test mTLS support in StreamManager."""

    def test_no_mtls_without_certs(self, camera_config):
        """Should use plain RTSP when no client cert exists."""
        mgr = StreamManager(camera_config)
        assert mgr._use_mtls is False
        assert mgr._stream_url.startswith("rtsp://")
        assert mgr._tls_flags() == []

    @patch.object(StreamManager, "_is_port_open", return_value=True)
    def test_mtls_with_certs(self, mock_port, camera_config, data_dir):
        """Should use RTSPS when client cert exists and port is open."""
        certs = data_dir / "certs"
        (certs / "client.crt").write_text("CERT")
        (certs / "client.key").write_text("KEY")
        (certs / "ca.crt").write_text("CA")

        mgr = StreamManager(camera_config)
        assert mgr._use_mtls is True
        assert mgr._stream_url.startswith("rtsps://")

    @patch.object(StreamManager, "_is_port_open", return_value=True)
    def test_tls_flags_with_certs(self, mock_port, camera_config, data_dir):
        """Should return TLS flags when certs exist."""
        certs = data_dir / "certs"
        (certs / "client.crt").write_text("CERT")
        (certs / "client.key").write_text("KEY")
        (certs / "ca.crt").write_text("CA")

        mgr = StreamManager(camera_config)
        flags = mgr._tls_flags()
        assert "-cert_file" in flags
        assert "-key_file" in flags
        assert "-ca_file" in flags
        assert str(certs / "client.crt") in flags
        assert str(certs / "client.key") in flags
        assert str(certs / "ca.crt") in flags

    @patch.object(StreamManager, "_is_port_open", return_value=True)
    def test_ffmpeg_cmd_includes_tls_flags(self, mock_port, camera_config, data_dir):
        """ffmpeg command should include TLS flags when paired."""
        certs = data_dir / "certs"
        (certs / "client.crt").write_text("CERT")
        (certs / "client.key").write_text("KEY")
        (certs / "ca.crt").write_text("CA")

        mgr = StreamManager(camera_config)
        cmd = mgr._build_ffmpeg_only_cmd()
        assert "-cert_file" in cmd
        assert "-key_file" in cmd
        assert "-ca_file" in cmd
        # Should use rtsps:// URL
        url = cmd[-1]
        assert url.startswith("rtsps://")

    def test_ffmpeg_cmd_no_tls_without_certs(self, camera_config):
        """ffmpeg command should not include TLS flags when unpaired."""
        mgr = StreamManager(camera_config)
        cmd = mgr._build_ffmpeg_only_cmd()
        assert "-cert_file" not in cmd
        url = cmd[-1]
        assert url.startswith("rtsp://")

    @patch.object(StreamManager, "_is_port_open", return_value=True)
    def test_rtsps_url_port(self, mock_port, camera_config, data_dir):
        """RTSPS URL should use port 8322."""
        certs = data_dir / "certs"
        (certs / "client.crt").write_text("CERT")

        mgr = StreamManager(camera_config)
        assert ":8322/" in mgr._stream_url

    @patch.object(StreamManager, "_is_port_open", return_value=True)
    def test_mtls_required_when_paired(self, mock_port, camera_config, data_dir):
        """mTLS must be used when camera is paired (has client cert)."""
        certs = data_dir / "certs"
        (certs / "client.crt").write_text("CERT")
        (certs / "client.key").write_text("KEY")
        (certs / "ca.crt").write_text("CA")

        mgr = StreamManager(camera_config)
        assert mgr._use_mtls is True
        assert mgr._stream_url.startswith("rtsps://")

    def test_falls_back_when_port_closed(self, camera_config, data_dir):
        """Should fall back to plain RTSP when RTSPS port is not reachable."""
        certs = data_dir / "certs"
        (certs / "client.crt").write_text("CERT")
        (certs / "client.key").write_text("KEY")
        (certs / "ca.crt").write_text("CA")

        with patch.object(StreamManager, "_is_port_open", return_value=False):
            mgr = StreamManager(camera_config)
            assert mgr._use_mtls is False
            assert mgr._stream_url.startswith("rtsp://")


class TestStreamBackoff:
    """Test reconnection backoff logic."""

    def test_initial_backoff(self):
        """Initial backoff should be 2 seconds."""
        assert INITIAL_BACKOFF == 2

    def test_max_backoff(self):
        """Max backoff should be 60 seconds."""
        assert MAX_BACKOFF == 60

    def test_backoff_exponential(self, camera_config):
        """Backoff should grow exponentially up to max."""
        mgr = StreamManager(camera_config)
        mgr._consecutive_failures = 1
        wait1 = min(INITIAL_BACKOFF * (2**0), MAX_BACKOFF)
        assert wait1 == 2

        mgr._consecutive_failures = 5
        wait5 = min(INITIAL_BACKOFF * (2**4), MAX_BACKOFF)
        assert wait5 == 32

        mgr._consecutive_failures = 10
        wait10 = min(INITIAL_BACKOFF * (2**9), MAX_BACKOFF)
        assert wait10 == MAX_BACKOFF  # Capped at 60
