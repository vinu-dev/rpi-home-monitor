# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Tests for the recorder service."""

from monitor.services.recorder_service import RecorderService


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

    def test_deletes_flat_layout_clip_via_dated_url(self, tmp_path):
        # Loop recorder writes clips directly under <cam>/ with flat
        # YYYYMMDD_HHMMSS.mp4 stems; the UI still hits the dated URL
        # shape — fallback should find + unlink the flat file.
        cam_dir = tmp_path / "cam-001"
        cam_dir.mkdir(parents=True)
        flat = cam_dir / "20260417_143000.mp4"
        flat.write_bytes(b"x")
        (cam_dir / "20260417_143000.thumb.jpg").write_bytes(b"t")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.delete_clip("cam-001", "2026-04-17", "20260417_143000.mp4") is True
        assert not flat.exists()
        assert not (cam_dir / "20260417_143000.thumb.jpg").exists()
        # Camera dir itself must survive.
        assert cam_dir.is_dir()


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


class TestFlatLayoutReads:
    """Loop-recorder produces <cam>/YYYYMMDD_HHMMSS.mp4 with no date
    subdir. All read methods must surface those clips — without these
    the Recordings page shows "No recordings found" even when the disk
    is full of footage (pre-fix symptom reported 2026-04-19).
    """

    def _make_flat(self, tmp_path, cam, stem, size=512):
        cam_dir = tmp_path / cam
        cam_dir.mkdir(parents=True, exist_ok=True)
        mp4 = cam_dir / f"{stem}.mp4"
        mp4.write_bytes(b"x" * size)
        return mp4

    def test_dates_from_flat_files(self, tmp_path):
        self._make_flat(tmp_path, "cam-001", "20260417_132730")
        self._make_flat(tmp_path, "cam-001", "20260417_170727")
        self._make_flat(tmp_path, "cam-001", "20260419_104351")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.get_dates_with_clips("cam-001") == ["2026-04-17", "2026-04-19"]

    def test_dates_merge_flat_and_dated(self, tmp_path):
        self._make_flat(tmp_path, "cam-001", "20260419_104351")
        _make_clip(tmp_path, "cam-001", "2026-04-18", "10-00-00")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        assert svc.get_dates_with_clips("cam-001") == ["2026-04-18", "2026-04-19"]

    def test_list_clips_flat(self, tmp_path):
        self._make_flat(tmp_path, "cam-001", "20260419_104351", size=1024)
        (tmp_path / "cam-001" / "20260419_104351.thumb.jpg").write_bytes(b"t")
        self._make_flat(tmp_path, "cam-001", "20260419_104051", size=2048)
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        clips = svc.list_clips("cam-001", "2026-04-19")
        assert len(clips) == 2
        assert clips[0].filename == "20260419_104051.mp4"
        assert clips[0].start_time == "10:40:51"
        assert clips[0].size_bytes == 2048
        assert clips[0].thumbnail == ""
        assert clips[1].filename == "20260419_104351.mp4"
        assert clips[1].thumbnail == "20260419_104351.thumb.jpg"
        assert clips[1].date == "2026-04-19"

    def test_list_clips_filters_by_date(self, tmp_path):
        self._make_flat(tmp_path, "cam-001", "20260417_132730")
        self._make_flat(tmp_path, "cam-001", "20260419_104351")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        clips = svc.list_clips("cam-001", "2026-04-19")
        assert len(clips) == 1
        assert clips[0].start_time == "10:43:51"

    def test_get_clip_path_falls_back_to_flat(self, tmp_path):
        flat = self._make_flat(tmp_path, "cam-001", "20260419_104351")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        path = svc.get_clip_path("cam-001", "2026-04-19", "20260419_104351.mp4")
        assert path == flat


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

    def test_returns_latest_flat(self, tmp_path):
        # Flat-layout clips must be returned by get_latest_clip too,
        # otherwise the dashboard's "Last activity" tile stays blank.
        cam_dir = tmp_path / "cam-001"
        cam_dir.mkdir()
        (cam_dir / "20260419_104051.mp4").write_bytes(b"x")
        (cam_dir / "20260419_104351.mp4").write_bytes(b"x")
        svc = RecorderService(str(tmp_path), str(tmp_path / "live"))
        clip = svc.get_latest_clip("cam-001")
        assert clip is not None
        assert clip.date == "2026-04-19"
        assert clip.start_time == "10:43:51"
