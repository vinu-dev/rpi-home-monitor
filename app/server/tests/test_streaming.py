"""Tests for monitor.services.streaming module."""
import os
import time
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path

from monitor.services.streaming import (
    StreamingService,
    create_recording_dirs,
    MEDIAMTX_URL,
    HLS_SEGMENT_DURATION,
    HLS_LIST_SIZE,
    CLIP_DURATION,
    SNAPSHOT_INTERVAL,
)


class TestStreamingService:
    """Test the video pipeline manager."""

    def test_init(self, tmp_path):
        """Should initialize with empty state."""
        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        assert svc.active_cameras == []

    def test_start_stop(self, tmp_path):
        """Should start and stop cleanly."""
        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.start()
        svc.stop()

    @patch("monitor.services.streaming.StreamingService._take_snapshot")
    @patch("subprocess.Popen")
    def test_start_camera(self, mock_popen, mock_snap, tmp_path):
        """start_camera should launch HLS and recorder ffmpeg processes."""
        proc = MagicMock()
        proc.pid = 1234
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.start()
        result = svc.start_camera("cam-abc123")

        assert result is True
        assert "cam-abc123" in svc.active_cameras
        # Should have launched 2 ffmpeg processes (HLS + recorder)
        assert mock_popen.call_count == 2
        svc.stop()

    @patch("subprocess.Popen")
    def test_start_camera_creates_dirs(self, mock_popen, tmp_path):
        """start_camera should create live and recording directories."""
        proc = MagicMock()
        proc.pid = 1234
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.start()
        svc.start_camera("cam-abc123")

        assert (tmp_path / "live" / "cam-abc123").is_dir()
        assert (tmp_path / "recordings" / "cam-abc123").is_dir()
        svc.stop()

    def test_start_camera_when_not_running(self, tmp_path):
        """Should refuse to start camera when service not running."""
        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        result = svc.start_camera("cam-abc123")
        assert result is False

    @patch("subprocess.Popen")
    def test_stop_camera(self, mock_popen, tmp_path):
        """stop_camera should terminate ffmpeg processes."""
        proc = MagicMock()
        proc.pid = 1234
        proc.poll.return_value = None
        proc.wait.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.start()
        svc.start_camera("cam-abc123")
        svc.stop_camera("cam-abc123")

        assert "cam-abc123" not in svc.active_cameras
        proc.terminate.assert_called()
        svc.stop()

    @patch("subprocess.Popen")
    def test_is_camera_active(self, mock_popen, tmp_path):
        """is_camera_active should reflect HLS process state."""
        proc = MagicMock()
        proc.pid = 1234
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.start()
        assert svc.is_camera_active("cam-abc123") is False

        svc.start_camera("cam-abc123")
        assert svc.is_camera_active("cam-abc123") is True
        svc.stop()

    def test_stop_nonexistent_camera(self, tmp_path):
        """stop_camera should not raise for unknown camera."""
        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.stop_camera("nonexistent")  # Should not raise

    @patch("subprocess.Popen")
    def test_stop_cleans_hls_segments(self, mock_popen, tmp_path):
        """stop_camera should remove stale HLS segment files."""
        proc = MagicMock()
        proc.pid = 1234
        proc.poll.return_value = None
        proc.wait.return_value = None
        mock_popen.return_value = proc

        live_dir = tmp_path / "live"
        cam_dir = live_dir / "cam-abc123"
        cam_dir.mkdir(parents=True)
        (cam_dir / "segment_001.ts").write_text("fake")
        (cam_dir / "segment_002.ts").write_text("fake")

        svc = StreamingService(
            live_dir=str(live_dir),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.start()
        svc.start_camera("cam-abc123")
        svc.stop_camera("cam-abc123")

        # HLS segments should be cleaned up
        assert not list(cam_dir.glob("segment_*.ts"))
        svc.stop()


class TestHLSPipeline:
    """Test HLS ffmpeg command construction."""

    @patch("subprocess.Popen")
    def test_hls_command(self, mock_popen, tmp_path):
        """HLS command should have correct flags."""
        proc = MagicMock()
        proc.pid = 1234
        mock_popen.return_value = proc

        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.start()
        svc._start_hls("cam-test", "rtsp://127.0.0.1:8554/cam-test")

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-nostdin" in cmd
        assert "-rtsp_transport" in cmd
        assert "tcp" in cmd
        assert "rtsp://127.0.0.1:8554/cam-test" in cmd
        assert "-f" in cmd
        assert "hls" in cmd
        assert "-hls_time" in cmd
        assert str(HLS_SEGMENT_DURATION) in cmd
        assert "-hls_list_size" in cmd
        assert str(HLS_LIST_SIZE) in cmd
        svc.stop()


class TestRecorderPipeline:
    """Test recording ffmpeg command construction."""

    @patch("subprocess.Popen")
    def test_recorder_command(self, mock_popen, tmp_path):
        """Recorder command should use segment muxer with 3-min duration."""
        proc = MagicMock()
        proc.pid = 1234
        mock_popen.return_value = proc

        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc.start()
        svc._start_recorder("cam-test", "rtsp://127.0.0.1:8554/cam-test")

        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-segment_time" in cmd
        idx = cmd.index("-segment_time")
        assert cmd[idx + 1] == str(CLIP_DURATION)
        assert "-segment_format" in cmd
        assert "mp4" in cmd
        assert "-strftime" in cmd
        svc.stop()


class TestSnapshot:
    """Test snapshot extraction."""

    @patch("subprocess.run")
    def test_take_snapshot(self, mock_run, tmp_path):
        """Should extract JPEG frame via ffmpeg."""
        mock_run.return_value = MagicMock(returncode=0)
        cam_live = tmp_path / "live" / "cam-test"
        cam_live.mkdir(parents=True)
        # Create the tmp file that ffmpeg would produce
        tmp_jpg = cam_live / "snapshot.tmp.jpg"
        tmp_jpg.write_bytes(b"\xff\xd8fake-jpeg")

        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        svc._take_snapshot("cam-test", "rtsp://127.0.0.1:8554/cam-test")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "ffmpeg" in cmd[0]
        assert "-frames:v" in cmd
        assert "1" in cmd

    @patch("subprocess.run")
    def test_snapshot_timeout(self, mock_run, tmp_path):
        """Should handle ffmpeg timeout gracefully."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 10)

        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        (tmp_path / "live" / "cam-test").mkdir(parents=True)
        svc._take_snapshot("cam-test", "rtsp://127.0.0.1:8554/cam-test")
        # Should not raise


class TestLaunchFFmpeg:
    """Test ffmpeg process launching."""

    @patch("subprocess.Popen", side_effect=FileNotFoundError)
    def test_ffmpeg_not_found(self, mock_popen, tmp_path):
        """Should return None when ffmpeg not installed."""
        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        result = svc._launch_ffmpeg(["ffmpeg"], "test")
        assert result is None

    @patch("subprocess.Popen", side_effect=OSError("some error"))
    def test_oserror(self, mock_popen, tmp_path):
        """Should return None on OS error."""
        svc = StreamingService(
            live_dir=str(tmp_path / "live"),
            recordings_dir=str(tmp_path / "recordings"),
        )
        result = svc._launch_ffmpeg(["ffmpeg"], "test")
        assert result is None


class TestCreateRecordingDirs:
    """Test recording directory creation."""

    def test_creates_date_dir(self, tmp_path):
        """Should create cam/<date> directory."""
        path = create_recording_dirs(str(tmp_path), "cam-test")
        assert path.is_dir()
        assert "cam-test" in str(path)

    def test_idempotent(self, tmp_path):
        """Should not fail if directory exists."""
        create_recording_dirs(str(tmp_path), "cam-test")
        create_recording_dirs(str(tmp_path), "cam-test")


class TestConstants:
    """Test module constants."""

    def test_mediamtx_url(self):
        assert MEDIAMTX_URL == "rtsp://127.0.0.1:8554"

    def test_hls_segment_duration(self):
        assert HLS_SEGMENT_DURATION == 2

    def test_hls_list_size(self):
        assert HLS_LIST_SIZE == 5

    def test_clip_duration(self):
        assert CLIP_DURATION == 180

    def test_snapshot_interval(self):
        assert SNAPSHOT_INTERVAL == 30
