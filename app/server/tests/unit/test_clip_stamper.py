# REQ: SWR-029, SWR-030; RISK: RISK-014, RISK-017; SEC: SC-014, SC-020; TEST: TC-026, TC-027
"""Unit tests for the clip timestamp stamper."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from monitor.models import Camera, ServerMeta
from monitor.services.clip_stamper import ClipStamper, stamp_sentinel_path


def _camera() -> Camera:
    return Camera(
        id="cam-001", name="Front Door", status="online", sensor_model="IMX219"
    )


def _server_meta() -> ServerMeta:
    return ServerMeta(hostname="home-monitor", server_version="1.5.0")


def _stamped_probe() -> dict:
    return {
        "format": {
            "duration": "180.0",
            "tags": {
                "title": "Front Door - 2026-04-20T14:00:00Z",
                "creation_time": "2026-04-20T14:00:00.000000Z",
            },
        },
        "streams": [{"codec_type": "video"}, {"codec_type": "subtitle"}],
    }


def _unstamped_probe() -> dict:
    return {
        "format": {
            "duration": "180.0",
            "tags": {},
        },
        "streams": [{"codec_type": "video"}],
    }


def test_stamp_skips_when_probe_and_sentinel_show_existing_stamp(tmp_path):
    clip = tmp_path / "cam-001" / "20260420_140000.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"source")
    stamp_sentinel_path(clip).write_text("ok\n", encoding="utf-8")
    stamper = ClipStamper()

    with (
        patch("monitor.services.clip_stamper.shutil.which", return_value="tool"),
        patch.object(stamper, "_probe_clip", return_value=_stamped_probe()),
    ):
        result = stamper.stamp(clip, _camera(), _server_meta())

    assert result.ok is True
    assert result.skipped is True
    assert result.stamped is True
    assert result.reason == "already-stamped"


def test_stamp_returns_missing_ffmpeg_when_tool_unavailable(tmp_path):
    clip = tmp_path / "cam-001" / "20260420_140000.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"source")
    stamper = ClipStamper(ffmpeg_path="ffmpeg", ffprobe_path="ffprobe")

    with patch(
        "monitor.services.clip_stamper.shutil.which",
        side_effect=lambda tool: "ffprobe" if tool == "ffprobe" else None,
    ):
        result = stamper.stamp(clip, _camera(), _server_meta())

    assert result.ok is False
    assert result.reason == "ffmpeg-missing"


def test_stamp_treats_invalid_timestamp_filename_as_unsupported(tmp_path):
    clip = tmp_path / "cam-001" / "20261399_999999.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"source")
    stamper = ClipStamper()

    with (
        patch("monitor.services.clip_stamper.shutil.which", return_value="tool"),
        patch.object(stamper, "_probe_clip", return_value=_unstamped_probe()),
    ):
        result = stamper.stamp(clip, _camera(), _server_meta())

    assert result.ok is False
    assert result.reason == "unsupported-filename"


def test_stamp_runs_remux_and_writes_sentinel(tmp_path):
    clip = tmp_path / "cam-001" / "20260420_140000.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"source")
    stamper = ClipStamper(clock_state_provider=lambda: {"ntp_synchronized": True})

    def _run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(b"stamped")
        return SimpleNamespace(returncode=0, stderr=b"")

    with (
        patch("monitor.services.clip_stamper.shutil.which", return_value="tool"),
        patch("monitor.services.clip_stamper.subprocess.run", side_effect=_run),
        patch.object(
            stamper,
            "_probe_clip",
            side_effect=[_unstamped_probe(), _stamped_probe()],
        ),
    ):
        result = stamper.stamp(clip, _camera(), _server_meta())

    assert result.ok is True
    assert result.reason == "stamped"
    assert result.stamped is True
    assert stamp_sentinel_path(clip).exists()
    assert clip.read_bytes() == b"stamped"


def test_build_ffmetadata_adds_minute_chapters():
    stamper = ClipStamper()
    text = stamper._build_ffmetadata(
        started_at=stamper._parse_clip_timestamp(Path("20260420_140000.mp4")),
        duration_seconds=125,
    )
    assert text.count("[CHAPTER]") == 3
    assert "title=2026-04-20 14:00 UTC" in text
    assert "title=2026-04-20 14:02 UTC" in text
