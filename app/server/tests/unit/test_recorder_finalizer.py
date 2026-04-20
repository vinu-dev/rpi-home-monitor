"""Unit tests for recorder segment-completion helpers.

Historical: ``finalize_completed_segments`` renamed ``.mp4.part`` files
to ``.mp4`` after ffmpeg closed them. ffmpeg 6.1.4's segment muxer
rejects the ``.mp4.part`` extension, so the recorder now writes
``.mp4`` directly and the finalizer is a kept-alive no-op. The
"safe to read" signal now comes from ``completed_segment_names``
(files whose names appear in ``.segments.log``).
"""

from __future__ import annotations

import pytest

from monitor.services.streaming_service import (
    completed_segment_names,
    finalize_completed_segments,
)


@pytest.fixture
def cam_dir(tmp_path):
    d = tmp_path / "cam-001"
    d.mkdir()
    return d


def _append_log(log_path, *lines: str):
    with open(log_path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


class TestCompletedSegmentNames:
    def test_empty_when_log_missing(self, cam_dir):
        assert completed_segment_names(cam_dir / ".nope") == set()

    def test_collects_basenames(self, cam_dir):
        log = cam_dir / ".segments.log"
        _append_log(log, "20260420_140000.mp4", "20260420_140300.mp4")
        assert completed_segment_names(log) == {
            "20260420_140000.mp4",
            "20260420_140300.mp4",
        }

    def test_strips_full_paths(self, cam_dir):
        """ffmpeg may write full paths depending on how we invoke it."""
        log = cam_dir / ".segments.log"
        _append_log(log, str(cam_dir / "20260420_140000.mp4"))
        assert completed_segment_names(log) == {"20260420_140000.mp4"}

    def test_ignores_blank_lines(self, cam_dir):
        log = cam_dir / ".segments.log"
        log.write_bytes(b"\n  \n20260420_140000.mp4\n\n")
        assert completed_segment_names(log) == {"20260420_140000.mp4"}


class TestFinalizeLegacyNoop:
    """The renamer is now a no-op — but the offset bookkeeping stays
    compatible so old call sites (and the background poller) keep
    working without changes."""

    def test_returns_current_log_size(self, cam_dir):
        log = cam_dir / ".segments.log"
        _append_log(log, "20260420_140000.mp4")
        off = finalize_completed_segments(cam_dir, log, 0)
        assert off == log.stat().st_size

    def test_missing_log_returns_passed_offset(self, cam_dir):
        assert finalize_completed_segments(cam_dir, cam_dir / ".nope", 42) == 42

    def test_offset_is_monotonic(self, cam_dir):
        log = cam_dir / ".segments.log"
        _append_log(log, "20260420_140000.mp4")
        off = finalize_completed_segments(cam_dir, log, 0)
        _append_log(log, "20260420_140300.mp4")
        off2 = finalize_completed_segments(cam_dir, log, off)
        assert off2 >= off
        assert off2 == log.stat().st_size
