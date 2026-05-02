# REQ: SWR-006, SWR-030; RISK: RISK-001, RISK-014; SEC: SC-002, SC-014; TEST: TC-001, TC-027
"""Tests for monitor.services.streaming_service (post ADR-0017 rewrite)."""

from unittest.mock import MagicMock, patch

from monitor.services.streaming_service import (
    MEDIAMTX_URL,
    SNAPSHOT_INTERVAL,
    StreamingService,
    create_recording_dirs,
)


class TestStreamingServiceLifecycle:
    def test_init_has_empty_state(self, tmp_path):
        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        assert svc.active_cameras == []
        assert svc.recordings_dir.endswith("rec")

    def test_start_stop_clean(self, tmp_path):
        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        svc.stop()


class TestSnapshotPipeline:
    """The rewrite keeps a single long-lived snapshot ffmpeg per camera."""

    @patch("subprocess.Popen")
    def test_start_camera_launches_one_snapshot_ffmpeg(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1234
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        assert svc.start_camera("cam-abc") is True
        # Exactly one ffmpeg (snapshot) — no HLS muxer, no recorder.
        assert mock_popen.call_count == 1
        assert "cam-abc" in svc.active_cameras
        svc.stop()

    @patch("subprocess.Popen")
    def test_snapshot_command_uses_update_and_fps(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        svc.start_camera("cam-x")
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "ffmpeg"
        assert "-update" in cmd
        assert "1" in cmd
        # fps=1/30 string appears somewhere
        assert any(f"fps=1/{SNAPSHOT_INTERVAL}" in c for c in cmd)
        svc.stop()

    @patch("subprocess.Popen")
    def test_start_camera_creates_live_dir(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        svc.start_camera("cam-abc")
        assert (tmp_path / "live" / "cam-abc").is_dir()
        svc.stop()

    def test_start_camera_refused_when_not_running(self, tmp_path):
        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        assert svc.start_camera("cam-abc") is False

    @patch("subprocess.Popen")
    def test_stop_camera_marks_intent_stopped(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = None
        proc.wait.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        svc.start_camera("cam-abc")
        svc.stop_camera("cam-abc")
        assert svc._snap_intent["cam-abc"] == "stopped"
        assert "cam-abc" not in svc.active_cameras
        svc.stop()


class TestRecorderPipeline:
    """The recorder is now owned by StreamingService but driven by scheduler."""

    @patch("subprocess.Popen")
    def test_start_recorder_uses_c_copy_and_segment(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        started = svc.start_recorder("cam-x", f"{MEDIAMTX_URL}/cam-x")
        assert started is True
        cmd = mock_popen.call_args[0][0]
        assert "-c" in cmd and "copy" in cmd
        assert "-f" in cmd and "segment" in cmd
        # ffmpeg writes .mp4 directly — the segment muxer refuses
        # ``.mp4.part`` (see streaming_service.py). Fragmented-mp4
        # movflags make each in-progress segment playable as it grows
        # so a motion event whose timestamp falls in the current clip
        # can be seeked into immediately.
        assert any(arg.endswith(".mp4") for arg in cmd)
        assert not any(arg.endswith(".mp4.part") for arg in cmd)
        assert "-segment_format_options" in cmd
        fmt_opts = cmd[cmd.index("-segment_format_options") + 1]
        assert "frag_keyframe" in fmt_opts
        assert "empty_moov" in fmt_opts
        assert "-segment_list" in cmd
        svc.stop()

    @patch("subprocess.Popen")
    def test_start_recorder_is_idempotent(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        svc.start_recorder("cam-x", f"{MEDIAMTX_URL}/cam-x")
        assert svc.start_recorder("cam-x", f"{MEDIAMTX_URL}/cam-x") is False
        assert mock_popen.call_count == 1
        svc.stop()

    @patch("subprocess.Popen")
    def test_is_recording_reflects_process_state(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        assert svc.is_recording("cam-x") is False
        svc.start_recorder("cam-x", f"{MEDIAMTX_URL}/cam-x")
        assert svc.is_recording("cam-x") is True
        svc.stop()


class TestWatchdogIntent:
    """Deliberately-stopped processes are NOT restarted by the watchdog."""

    @patch("subprocess.Popen")
    def test_dead_and_stopped_is_not_restarted(self, mock_popen, tmp_path):
        dead = MagicMock()
        dead.poll.return_value = 0  # exited
        dead.pid = 99
        mock_popen.return_value = dead

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        svc.start_camera("cam-x")
        # Mark intent as stopped, then let watchdog see a dead proc.
        svc._snap_intent["cam-x"] = "stopped"

        calls_before = mock_popen.call_count
        svc._check_processes()
        assert mock_popen.call_count == calls_before  # no restart
        svc.stop()

    @patch("subprocess.Popen")
    def test_dead_but_wanted_is_restarted(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = 1  # exited unexpectedly
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        svc.start_camera("cam-x")
        calls_before = mock_popen.call_count

        svc._check_processes()
        assert mock_popen.call_count > calls_before
        svc.stop()


class TestCreateRecordingDirs:
    def test_creates_flat_per_camera_dir(self, tmp_path):
        p = create_recording_dirs(str(tmp_path), "cam-x")
        assert p.is_dir()
        assert p.name == "cam-x"

    def test_idempotent(self, tmp_path):
        create_recording_dirs(str(tmp_path), "cam-x")
        create_recording_dirs(str(tmp_path), "cam-x")


class TestLaunchFFmpegErrorPaths:
    @patch("subprocess.Popen", side_effect=FileNotFoundError)
    def test_ffmpeg_not_found(self, _mock, tmp_path):
        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        assert svc._launch_ffmpeg(["ffmpeg"], "test") is None

    @patch("subprocess.Popen", side_effect=OSError("boom"))
    def test_oserror(self, _mock, tmp_path):
        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        assert svc._launch_ffmpeg(["ffmpeg"], "test") is None


class TestUpdateRecordingsDir:
    @patch("subprocess.Popen")
    def test_restarts_active_recorders(self, mock_popen, tmp_path):
        proc = MagicMock()
        proc.pid = 1
        proc.poll.return_value = None
        proc.wait.return_value = None
        mock_popen.return_value = proc

        svc = StreamingService(str(tmp_path / "live"), str(tmp_path / "rec"))
        svc.start()
        svc.start_recorder("cam-x", f"{MEDIAMTX_URL}/cam-x")
        calls_before = mock_popen.call_count
        new_dir = tmp_path / "rec2"
        new_dir.mkdir()
        svc.update_recordings_dir(str(new_dir))
        # Recorder should have been stopped & restarted.
        assert mock_popen.call_count > calls_before
        assert svc.recordings_dir == str(new_dir)
        svc.stop()
