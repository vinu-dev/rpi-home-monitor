"""Tests for SnapshotExtractor — ADR-0027 §Snapshot pipeline."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from monitor.services.snapshot_extractor import SnapshotExtractor


@pytest.fixture
def recordings(tmp_path):
    rec = tmp_path / "recordings"
    rec.mkdir()
    return rec


def _make_clip(recordings, cam, date, name, content=b"\x00" * 1024):
    """Drop a fake .mp4 stub at the expected nested path."""
    d = recordings / cam / date
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(content)
    return p


class TestPathHandling:
    def test_returns_none_for_missing_clip_file(self, recordings):
        sx = SnapshotExtractor(recordings)
        result = sx.extract_for_clip(
            {"camera_id": "cam-x", "date": "2026-05-02", "filename": "missing.mp4"}
        )
        assert result is None

    def test_returns_none_for_invalid_clip_ref(self, recordings):
        sx = SnapshotExtractor(recordings)
        # Missing required fields.
        assert sx.extract_for_clip({}) is None
        assert sx.extract_for_clip({"camera_id": "x"}) is None
        assert sx.extract_for_clip({"camera_id": "x", "date": "2026-05-02"}) is None
        # Wrong extension.
        _make_clip(recordings, "cam-x", "2026-05-02", "fake.txt")
        assert (
            sx.extract_for_clip(
                {"camera_id": "cam-x", "date": "2026-05-02", "filename": "fake.txt"}
            )
            is None
        )
        # None clip_ref.
        assert sx.extract_for_clip(None) is None  # type: ignore[arg-type]

    def test_idempotent_when_jpg_already_exists(self, recordings):
        sx = SnapshotExtractor(recordings)
        clip = _make_clip(recordings, "cam-x", "2026-05-02", "abc.mp4")
        # Pretend a previous run already produced the snapshot.
        snap = clip.with_suffix(".jpg")
        snap.write_bytes(b"\xff\xd8" + b"\x00" * 100)  # JPEG magic + data
        # Patch ffmpeg so we can verify it was NOT called.
        with (
            patch("monitor.services.snapshot_extractor.subprocess.run") as run,
            patch(
                "monitor.services.snapshot_extractor.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
        ):
            result = sx.extract_for_clip(
                {"camera_id": "cam-x", "date": "2026-05-02", "filename": "abc.mp4"}
            )
        assert result == "cam-x/2026-05-02/abc.jpg".replace(
            "/", "\\"
        ) or result.endswith("abc.jpg")
        run.assert_not_called()


class TestFfmpegMissing:
    def test_warns_once_when_ffmpeg_not_on_path(self, recordings, caplog):
        _make_clip(recordings, "cam-x", "2026-05-02", "abc.mp4")
        with patch(
            "monitor.services.snapshot_extractor.shutil.which", return_value=None
        ):
            sx = SnapshotExtractor(recordings, ffmpeg_path="/nonexistent/ffmpeg")
            r1 = sx.extract_for_clip(
                {"camera_id": "cam-x", "date": "2026-05-02", "filename": "abc.mp4"}
            )
            r2 = sx.extract_for_clip(
                {"camera_id": "cam-x", "date": "2026-05-02", "filename": "abc.mp4"}
            )
        assert r1 is None
        assert r2 is None
        # Only one warning should have been emitted across two calls.
        warnings = [r for r in caplog.records if "ffmpeg not on PATH" in r.getMessage()]
        assert len(warnings) == 1


class TestExtractionSuccess:
    def test_runs_ffmpeg_and_returns_path_on_success(self, recordings):
        clip = _make_clip(recordings, "cam-x", "2026-05-02", "abc.mp4")
        snap = clip.with_suffix(".jpg")

        def fake_run(cmd, **kwargs):
            # Simulate ffmpeg by writing a non-empty file at the
            # output path it was given.
            out_path = cmd[-1]
            with open(out_path, "wb") as f:
                f.write(b"\xff\xd8" + b"\x00" * 200)
            return type("R", (), {"returncode": 0, "stderr": b""})()

        with (
            patch(
                "monitor.services.snapshot_extractor.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "monitor.services.snapshot_extractor.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            sx = SnapshotExtractor(recordings)
            result = sx.extract_for_clip(
                {"camera_id": "cam-x", "date": "2026-05-02", "filename": "abc.mp4"}
            )
        assert result is not None
        assert result.endswith("abc.jpg")
        assert snap.exists()
        assert snap.stat().st_size > 0


class TestExtractionFailure:
    def test_returns_none_on_ffmpeg_nonzero_rc(self, recordings):
        _make_clip(recordings, "cam-x", "2026-05-02", "abc.mp4")
        with (
            patch(
                "monitor.services.snapshot_extractor.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "monitor.services.snapshot_extractor.subprocess.run",
                return_value=type(
                    "R", (), {"returncode": 1, "stderr": b"corrupt input"}
                )(),
            ),
        ):
            sx = SnapshotExtractor(recordings)
            result = sx.extract_for_clip(
                {"camera_id": "cam-x", "date": "2026-05-02", "filename": "abc.mp4"}
            )
        assert result is None

    def test_cleans_up_zero_byte_stub_on_failure(self, recordings):
        clip = _make_clip(recordings, "cam-x", "2026-05-02", "abc.mp4")
        snap = clip.with_suffix(".jpg")

        def fake_run(cmd, **kwargs):
            # ffmpeg failures sometimes leave a zero-byte file behind;
            # simulate that.
            out_path = cmd[-1]
            with open(out_path, "wb"):
                pass
            return type("R", (), {"returncode": 1, "stderr": b""})()

        with (
            patch(
                "monitor.services.snapshot_extractor.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "monitor.services.snapshot_extractor.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            sx = SnapshotExtractor(recordings)
            result = sx.extract_for_clip(
                {"camera_id": "cam-x", "date": "2026-05-02", "filename": "abc.mp4"}
            )
        assert result is None
        assert not snap.exists()  # zero-byte stub got cleaned up

    def test_handles_timeout_silently(self, recordings):
        import subprocess as sp

        _make_clip(recordings, "cam-x", "2026-05-02", "abc.mp4")
        with (
            patch(
                "monitor.services.snapshot_extractor.shutil.which",
                return_value="/usr/bin/ffmpeg",
            ),
            patch(
                "monitor.services.snapshot_extractor.subprocess.run",
                side_effect=sp.TimeoutExpired(cmd="ffmpeg", timeout=10),
            ),
        ):
            sx = SnapshotExtractor(recordings)
            result = sx.extract_for_clip(
                {"camera_id": "cam-x", "date": "2026-05-02", "filename": "abc.mp4"}
            )
        assert result is None  # timed out → None, no raise
