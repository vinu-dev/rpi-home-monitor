"""Unit tests for finalize_completed_segments — the .mp4.part → .mp4 renamer."""

from __future__ import annotations

import pytest

from monitor.services.streaming_service import finalize_completed_segments


@pytest.fixture
def cam_dir(tmp_path):
    d = tmp_path / "cam-001"
    d.mkdir()
    return d


def _touch(path, content: bytes = b"fake-mp4-body"):
    path.write_bytes(content)


def _append_log(log_path, *lines: str):
    with open(log_path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


class TestFinalizeSingleSegment:
    def test_renames_part_to_mp4(self, cam_dir):
        log = cam_dir / ".segments.log"
        part = cam_dir / "20260419_143000.mp4.part"
        _touch(part)
        _append_log(log, "20260419_143000.mp4.part")

        new_offset = finalize_completed_segments(cam_dir, log, 0)

        assert (cam_dir / "20260419_143000.mp4").exists()
        assert not part.exists()
        assert new_offset > 0

    def test_accepts_bare_mp4_name_in_log(self, cam_dir):
        """ffmpeg sometimes writes the base name (without .part) — still work."""
        log = cam_dir / ".segments.log"
        part = cam_dir / "20260419_143000.mp4.part"
        _touch(part)
        _append_log(log, "20260419_143000.mp4")

        finalize_completed_segments(cam_dir, log, 0)
        assert (cam_dir / "20260419_143000.mp4").exists()
        assert not part.exists()

    def test_accepts_full_path_in_log(self, cam_dir):
        log = cam_dir / ".segments.log"
        part = cam_dir / "20260419_143000.mp4.part"
        _touch(part)
        full = str(part)
        _append_log(log, full)

        finalize_completed_segments(cam_dir, log, 0)
        assert (cam_dir / "20260419_143000.mp4").exists()
        assert not part.exists()


class TestIncrementalOffset:
    def test_later_call_only_processes_new_lines(self, cam_dir):
        log = cam_dir / ".segments.log"
        part1 = cam_dir / "20260419_143000.mp4.part"
        part2 = cam_dir / "20260419_143300.mp4.part"
        _touch(part1)
        _append_log(log, "20260419_143000.mp4.part")

        off = finalize_completed_segments(cam_dir, log, 0)
        assert (cam_dir / "20260419_143000.mp4").exists()

        # Recorder flushes a second segment. part2 only exists now.
        _touch(part2)
        _append_log(log, "20260419_143300.mp4.part")

        off2 = finalize_completed_segments(cam_dir, log, off)
        assert (cam_dir / "20260419_143300.mp4").exists()
        assert off2 > off

    def test_no_new_lines_keeps_offset_stable(self, cam_dir):
        log = cam_dir / ".segments.log"
        part = cam_dir / "20260419_143000.mp4.part"
        _touch(part)
        _append_log(log, "20260419_143000.mp4.part")

        off = finalize_completed_segments(cam_dir, log, 0)
        off2 = finalize_completed_segments(cam_dir, log, off)
        assert off2 == off


class TestMissingInputs:
    def test_no_log_file_is_noop(self, cam_dir):
        # No segments.log yet — just return offset unchanged.
        new_off = finalize_completed_segments(cam_dir, cam_dir / ".missing", 0)
        assert new_off == 0

    def test_log_line_references_missing_part_is_skipped(self, cam_dir):
        """Log lists a file that doesn't exist on disk — skip, don't raise."""
        log = cam_dir / ".segments.log"
        _append_log(log, "doesnotexist.mp4.part")

        new_off = finalize_completed_segments(cam_dir, log, 0)
        # We still advance the offset (the line was consumed) but no file created.
        assert new_off > 0
        assert not (cam_dir / "doesnotexist.mp4").exists()

    def test_empty_line_ignored(self, cam_dir):
        log = cam_dir / ".segments.log"
        log.write_bytes(b"\n\n   \n")
        new_off = finalize_completed_segments(cam_dir, log, 0)
        # Entire log consumed, no files created.
        assert new_off == log.stat().st_size

    def test_non_mp4_line_ignored(self, cam_dir):
        log = cam_dir / ".segments.log"
        _append_log(log, "some-other.txt")
        finalize_completed_segments(cam_dir, log, 0)
        assert not list(cam_dir.glob("*.mp4"))


class TestAtomicReplace:
    def test_handles_pre_existing_mp4(self, cam_dir):
        """If a previous rename left both .mp4 and .mp4.part, replace wins."""
        log = cam_dir / ".segments.log"
        part = cam_dir / "20260419_143000.mp4.part"
        final = cam_dir / "20260419_143000.mp4"
        _touch(part, b"new-content")
        _touch(final, b"stale-old-content")
        _append_log(log, "20260419_143000.mp4.part")

        finalize_completed_segments(cam_dir, log, 0)

        assert final.exists()
        assert not part.exists()
        # Content should be the new one (os.replace semantics).
        assert final.read_bytes() == b"new-content"
