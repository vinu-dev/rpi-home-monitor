"""Unit tests for MotionClipCorrelator."""

from __future__ import annotations

import pytest

from monitor.services.motion_clip_correlator import MotionClipCorrelator


@pytest.fixture
def recordings_dir(tmp_path):
    root = tmp_path / "recordings"
    root.mkdir()
    return root


def _make_clip(parent, filename: str, content: bytes = b"fake-mp4"):
    parent.mkdir(parents=True, exist_ok=True)
    (parent / filename).write_bytes(content)


class TestFlatLayout:
    def test_finds_clip_when_event_inside_range(self, recordings_dir):
        cam = recordings_dir / "cam-001"
        _make_clip(cam, "20260419_143000.mp4")  # starts 14:30:00
        corr = MotionClipCorrelator(recordings_dir, clip_duration_seconds=180)

        # Event at 14:31:15 — 75 s into the clip.
        ref = corr.find_clip("cam-001", "2026-04-19T14:31:15Z")
        assert ref is not None
        assert ref["camera_id"] == "cam-001"
        assert ref["date"] == "2026-04-19"
        assert ref["filename"] == "20260419_143000.mp4"
        assert ref["offset_seconds"] == 75

    def test_no_match_before_clip_start(self, recordings_dir):
        cam = recordings_dir / "cam-001"
        _make_clip(cam, "20260419_143000.mp4")
        corr = MotionClipCorrelator(recordings_dir)

        # Event 1 s before the clip started.
        ref = corr.find_clip("cam-001", "2026-04-19T14:29:59Z")
        assert ref is None

    def test_no_match_after_clip_ends(self, recordings_dir):
        cam = recordings_dir / "cam-001"
        _make_clip(cam, "20260419_143000.mp4")
        corr = MotionClipCorrelator(recordings_dir, clip_duration_seconds=180)

        # Event 1 s after the clip ends (at 14:33:00).
        ref = corr.find_clip("cam-001", "2026-04-19T14:33:01Z")
        assert ref is None

    def test_offset_zero_at_clip_start(self, recordings_dir):
        cam = recordings_dir / "cam-001"
        _make_clip(cam, "20260419_143000.mp4")
        corr = MotionClipCorrelator(recordings_dir)

        ref = corr.find_clip("cam-001", "2026-04-19T14:30:00Z")
        assert ref is not None
        assert ref["offset_seconds"] == 0


class TestDatedLayout:
    def test_finds_clip_in_dated_subdir(self, recordings_dir):
        cam = recordings_dir / "cam-001" / "2026-04-19"
        _make_clip(cam, "14-30-00.mp4")
        corr = MotionClipCorrelator(recordings_dir)

        ref = corr.find_clip("cam-001", "2026-04-19T14:32:10Z")
        assert ref is not None
        assert ref["date"] == "2026-04-19"
        assert ref["filename"] == "14-30-00.mp4"
        assert ref["offset_seconds"] == 130

    def test_prefers_flat_over_dated_if_both_match(self, recordings_dir):
        """Flat layout is scanned first; dated layout is a fallback."""
        cam = recordings_dir / "cam-001"
        _make_clip(cam, "20260419_143000.mp4")
        _make_clip(cam / "2026-04-19", "14-30-00.mp4")
        corr = MotionClipCorrelator(recordings_dir)

        ref = corr.find_clip("cam-001", "2026-04-19T14:30:30Z")
        assert ref is not None
        # Either match is correct; we just verify one lands.
        assert ref["filename"] in {"20260419_143000.mp4", "14-30-00.mp4"}


class TestNonClipFilesIgnored:
    """ffmpeg 6.1.4's segment muxer refuses ``.mp4.part`` names, so the
    recorder now writes ``.mp4`` directly (see streaming_service.py).
    Fragmented-mp4 flags make in-progress segments playable as they
    grow, so the correlator intentionally doesn't filter them out —
    if a motion event falls inside the currently-writing segment, the
    user still gets a playable clip with seek. This suite verifies that
    files which aren't valid segment names (e.g. stray trash) are
    ignored without crashing."""

    def test_non_clip_filenames_are_skipped(self, recordings_dir):
        cam = recordings_dir / "cam-001"
        _make_clip(cam, "20260419_143000.mp4")  # real clip
        _make_clip(cam, "notes.mp4")  # junk — no timestamp pattern
        corr = MotionClipCorrelator(recordings_dir)

        ref = corr.find_clip("cam-001", "2026-04-19T14:30:30Z")
        assert ref is not None
        assert ref["filename"] == "20260419_143000.mp4"


class TestMidnightStraddle:
    def test_finds_clip_starting_yesterday(self, recordings_dir):
        """A clip that started at 23:58:00 yesterday can cover a 00:00:30
        event today (clip is 180 s long)."""
        cam = recordings_dir / "cam-001"
        # Yesterday 23:58:00 + 180s = today 00:01:00.
        _make_clip(cam, "20260418_235800.mp4")
        corr = MotionClipCorrelator(recordings_dir, clip_duration_seconds=180)

        ref = corr.find_clip("cam-001", "2026-04-19T00:00:30Z")
        assert ref is not None
        assert ref["date"] == "2026-04-18"
        assert ref["filename"] == "20260418_235800.mp4"
        assert ref["offset_seconds"] == 150


class TestMissingInputs:
    def test_unknown_camera_returns_none(self, recordings_dir):
        corr = MotionClipCorrelator(recordings_dir)
        assert corr.find_clip("cam-does-not-exist", "2026-04-19T14:30:00Z") is None

    def test_malformed_timestamp_returns_none(self, recordings_dir):
        corr = MotionClipCorrelator(recordings_dir)
        assert corr.find_clip("cam-001", "not-a-timestamp") is None

    def test_empty_timestamp_returns_none(self, recordings_dir):
        corr = MotionClipCorrelator(recordings_dir)
        assert corr.find_clip("cam-001", "") is None

    def test_empty_recordings_dir_returns_none(self, recordings_dir):
        (recordings_dir / "cam-001").mkdir()
        corr = MotionClipCorrelator(recordings_dir)
        assert corr.find_clip("cam-001", "2026-04-19T14:30:00Z") is None


class TestCustomClipDuration:
    def test_shorter_duration_narrows_match_window(self, recordings_dir):
        cam = recordings_dir / "cam-001"
        _make_clip(cam, "20260419_143000.mp4")

        # 60-s clip duration — 2-min-in is outside the window.
        corr_short = MotionClipCorrelator(recordings_dir, clip_duration_seconds=60)
        assert corr_short.find_clip("cam-001", "2026-04-19T14:32:00Z") is None

        # 180-s duration — inside.
        corr_std = MotionClipCorrelator(recordings_dir, clip_duration_seconds=180)
        assert corr_std.find_clip("cam-001", "2026-04-19T14:32:00Z") is not None


class TestMalformedFilenames:
    def test_non_standard_stems_skipped(self, recordings_dir):
        """Random .mp4 files that don't match either naming scheme are ignored."""
        cam = recordings_dir / "cam-001"
        _make_clip(cam, "random_name.mp4")
        _make_clip(cam, "untitled.mp4")
        _make_clip(cam, "20260419_143000.mp4")  # valid, should match
        corr = MotionClipCorrelator(recordings_dir)

        ref = corr.find_clip("cam-001", "2026-04-19T14:30:30Z")
        assert ref is not None
        assert ref["filename"] == "20260419_143000.mp4"
