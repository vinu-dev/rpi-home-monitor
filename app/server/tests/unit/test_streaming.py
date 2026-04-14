"""Tests for monitor.services.streaming_service module."""

import os
import time
from unittest.mock import MagicMock, patch

from monitor.services.streaming_service import (
    CLIP_DURATION,
    HLS_LIST_SIZE,
    HLS_SEGMENT_DURATION,
    MEDIAMTX_URL,
    SNAPSHOT_INTERVAL,
    StreamingService,
    create_recording_dirs,
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

    @patch("monitor.services.streaming_service.StreamingService._take_snapshot")
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


class TestStaleClipDetection:
    """Test _is_recorder_stale — detects hung ffmpeg segment muxer."""

    def test_no_cam_dir_not_stale(self, tmp_path):
        """Missing camera directory is not stale (hasn't started yet)."""
        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        assert svc._is_recorder_stale("cam-nonexistent") is False

    def test_no_clips_not_stale(self, tmp_path):
        """Camera dir exists but no clips — first clip still recording."""
        rec = tmp_path / "rec"
        (rec / "cam1" / "2026-04-11").mkdir(parents=True)
        svc = StreamingService(str(tmp_path / "live"), str(rec))
        assert svc._is_recorder_stale("cam1") is False

    def test_recent_clip_not_stale(self, tmp_path):
        """Clip written within threshold is fine."""
        rec = tmp_path / "rec"
        cam_dir = rec / "cam1" / "2026-04-11"
        cam_dir.mkdir(parents=True)
        clip = cam_dir / "08-30-00.mp4"
        clip.write_bytes(b"\x00" * 100)
        # Touch it to now
        clip.touch()

        svc = StreamingService(str(tmp_path / "live"), str(rec))
        assert svc._is_recorder_stale("cam1") is False

    def test_old_clip_is_stale(self, tmp_path):
        """Clip older than 2 * clip_duration triggers stale detection."""
        rec = tmp_path / "rec"
        cam_dir = rec / "cam1" / "2026-04-11"
        cam_dir.mkdir(parents=True)
        clip = cam_dir / "06-00-00.mp4"
        clip.write_bytes(b"\x00" * 100)
        # Set mtime to 10 minutes ago (threshold = 2 * 180 = 360s)
        old_time = time.time() - 600
        os.utime(str(clip), (old_time, old_time))

        svc = StreamingService(str(tmp_path / "live"), str(rec))
        assert svc._is_recorder_stale("cam1") is True

    def test_stale_threshold_uses_clip_duration(self, tmp_path):
        """Custom clip_duration changes stale threshold."""
        rec = tmp_path / "rec"
        cam_dir = rec / "cam1" / "2026-04-11"
        cam_dir.mkdir(parents=True)
        clip = cam_dir / "08-00-00.mp4"
        clip.write_bytes(b"\x00" * 100)
        # Set mtime to 70 seconds ago
        os.utime(str(clip), (time.time() - 70, time.time() - 70))

        # With clip_duration=30, threshold = 2*30 = 60s → 70s is stale
        svc = StreamingService(str(tmp_path / "live"), str(rec), clip_duration=30)
        assert svc._is_recorder_stale("cam1") is True

        # With clip_duration=60, threshold = 2*60 = 120s → 70s is NOT stale
        svc2 = StreamingService(str(tmp_path / "live"), str(rec), clip_duration=60)
        assert svc2._is_recorder_stale("cam1") is False

    def test_multiple_clips_uses_newest(self, tmp_path):
        """Stale check uses the newest clip, not oldest."""
        rec = tmp_path / "rec"
        cam_dir = rec / "cam1" / "2026-04-11"
        cam_dir.mkdir(parents=True)

        old_clip = cam_dir / "06-00-00.mp4"
        old_clip.write_bytes(b"\x00" * 100)
        os.utime(str(old_clip), (time.time() - 600, time.time() - 600))

        new_clip = cam_dir / "08-30-00.mp4"
        new_clip.write_bytes(b"\x00" * 100)
        new_clip.touch()  # now

        svc = StreamingService(str(tmp_path / "live"), str(rec))
        assert svc._is_recorder_stale("cam1") is False


class TestWatchdogStaleRestart:
    """Test that _check_processes force-restarts stalled recorders."""

    @patch.object(StreamingService, "_start_recorder")
    @patch.object(StreamingService, "_start_hls")
    def test_stale_recorder_is_killed_and_restarted(self, mock_hls, mock_rec, tmp_path):
        """A live but stale recorder should be force-killed and restarted."""
        rec = tmp_path / "rec"
        cam_dir = rec / "cam1" / "2026-04-11"
        cam_dir.mkdir(parents=True)
        clip = cam_dir / "06-00-00.mp4"
        clip.write_bytes(b"\x00" * 100)
        os.utime(str(clip), (time.time() - 600, time.time() - 600))

        svc = StreamingService(str(tmp_path / "live"), str(rec))

        # Simulate live HLS and recorder processes
        fake_hls = MagicMock()
        fake_hls.poll.return_value = None  # alive
        fake_rec = MagicMock()
        fake_rec.poll.return_value = None  # alive but stale
        fake_rec.pid = 12345

        svc._hls_procs["cam1"] = fake_hls
        svc._rec_procs["cam1"] = fake_rec

        svc._check_processes()

        # Recorder should have been terminated and restarted
        fake_rec.terminate.assert_called_once()
        mock_rec.assert_called_once_with("cam1", f"{MEDIAMTX_URL}/cam1")
        # HLS should NOT have been restarted (it's alive)
        mock_hls.assert_not_called()

    @patch.object(StreamingService, "_start_recorder")
    @patch.object(StreamingService, "_start_hls")
    def test_healthy_recorder_not_restarted(self, mock_hls, mock_rec, tmp_path):
        """A live recorder with recent clips should not be restarted."""
        rec = tmp_path / "rec"
        cam_dir = rec / "cam1" / "2026-04-11"
        cam_dir.mkdir(parents=True)
        clip = cam_dir / "08-30-00.mp4"
        clip.write_bytes(b"\x00" * 100)
        clip.touch()  # recent

        svc = StreamingService(str(tmp_path / "live"), str(rec))

        fake_hls = MagicMock()
        fake_hls.poll.return_value = None
        fake_rec = MagicMock()
        fake_rec.poll.return_value = None

        svc._hls_procs["cam1"] = fake_hls
        svc._rec_procs["cam1"] = fake_rec

        svc._check_processes()

        # Neither should be restarted
        mock_rec.assert_not_called()
        mock_hls.assert_not_called()


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
