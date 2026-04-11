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
