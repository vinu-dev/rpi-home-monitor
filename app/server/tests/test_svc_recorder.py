"""Tests for the recorder service."""

from monitor.services.recorder import RecorderService


def _make_clip(base, camera_id, clip_date, time_str, size=1024):
    """Helper: create a fake clip file."""
    clip_dir = base / camera_id / clip_date
    clip_dir.mkdir(parents=True, exist_ok=True)
    mp4 = clip_dir / f"{time_str}.mp4"
    mp4.write_bytes(b"x" * size)
    return mp4


def _make_thumb(base, camera_id, clip_date, time_str):
    """Helper: create a fake thumbnail."""
    clip_dir = base / camera_id / clip_date
    clip_dir.mkdir(parents=True, exist_ok=True)
    thumb = clip_dir / f"{time_str}.thumb.jpg"
    thumb.write_bytes(b"thumb")
    return thumb


class TestListClips:
    """Test clip listing."""

    def test_empty_directory(self, tmp_path):
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        clips = svc.list_clips("cam-001", "2026-04-09")
        assert clips == []

    def test_lists_clips_sorted(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-00-00")
        _make_clip(tmp_path, "cam-001", "2026-04-09", "15-00-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        clips = svc.list_clips("cam-001", "2026-04-09")
        assert len(clips) == 3
        assert clips[0].start_time == "14:00:00"
        assert clips[1].start_time == "14:30:00"
        assert clips[2].start_time == "15:00:00"

    def test_clip_fields(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-30-00", size=2048)
        _make_thumb(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        clip = svc.list_clips("cam-001", "2026-04-09")[0]
        assert clip.camera_id == "cam-001"
        assert clip.filename == "14-30-00.mp4"
        assert clip.date == "2026-04-09"
        assert clip.size_bytes == 2048
        assert clip.thumbnail == "14-30-00.thumb.jpg"

    def test_no_thumbnail(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        clip = svc.list_clips("cam-001", "2026-04-09")[0]
        assert clip.thumbnail == ""

    def test_different_cameras(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-00-00")
        _make_clip(tmp_path, "cam-002", "2026-04-09", "14-00-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert len(svc.list_clips("cam-001", "2026-04-09")) == 1
        assert len(svc.list_clips("cam-002", "2026-04-09")) == 1


class TestGetClipPath:
    """Test clip path resolution."""

    def test_existing_clip(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        path = svc.get_clip_path("cam-001", "2026-04-09", "14-30-00.mp4")
        assert path is not None
        assert path.name == "14-30-00.mp4"

    def test_nonexistent_clip(self, tmp_path):
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.get_clip_path("cam-001", "2026-04-09", "nope.mp4") is None


class TestDeleteClip:
    """Test clip deletion."""

    def test_deletes_clip_and_thumb(self, tmp_path):
        mp4 = _make_clip(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        _make_thumb(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.delete_clip("cam-001", "2026-04-09", "14-30-00.mp4") is True
        assert not mp4.exists()

    def test_deletes_clip_without_thumb(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.delete_clip("cam-001", "2026-04-09", "14-30-00.mp4") is True

    def test_removes_empty_date_dir(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        svc.delete_clip("cam-001", "2026-04-09", "14-30-00.mp4")
        assert not (tmp_path / "cam-001" / "2026-04-09").exists()

    def test_keeps_date_dir_with_other_clips(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-30-00")
        _make_clip(tmp_path, "cam-001", "2026-04-09", "15-00-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        svc.delete_clip("cam-001", "2026-04-09", "14-30-00.mp4")
        assert (tmp_path / "cam-001" / "2026-04-09").exists()

    def test_returns_false_for_missing(self, tmp_path):
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.delete_clip("cam-001", "2026-04-09", "nope.mp4") is False


class TestGetDatesWithClips:
    """Test date listing."""

    def test_no_clips(self, tmp_path):
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.get_dates_with_clips("cam-001") == []

    def test_lists_dates(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-07", "10-00-00")
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-00-00")
        _make_clip(tmp_path, "cam-001", "2026-04-08", "12-00-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        dates = svc.get_dates_with_clips("cam-001")
        assert dates == ["2026-04-07", "2026-04-08", "2026-04-09"]


class TestGetLatestClip:
    """Test latest clip retrieval."""

    def test_no_clips(self, tmp_path):
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.get_latest_clip("cam-001") is None

    def test_returns_latest(self, tmp_path):
        _make_clip(tmp_path, "cam-001", "2026-04-08", "10-00-00")
        _make_clip(tmp_path, "cam-001", "2026-04-09", "14-00-00")
        _make_clip(tmp_path, "cam-001", "2026-04-09", "15-30-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        clip = svc.get_latest_clip("cam-001")
        assert clip.date == "2026-04-09"
        assert clip.start_time == "15:30:00"
