"""Unit tests for MotionRunner — frame_reader hook + poster mocked.

Production path reads from a pipe fd fed by ffmpeg; tests inject a
``frame_reader`` generator instead so no real fd work is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from camera_streamer.motion import MotionConfig
from camera_streamer.motion_runner import MotionRunner


class _FakePoster:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        return True


def _blank(level=128):
    return np.full((240, 320), level, dtype=np.uint8)


def _moving(x: int, level=128, block_level=255, block_size=80):
    """Block at column x. Three-frame differencing only fires on
    inter-frame motion, so tests must advance x between calls."""
    frame = np.full((240, 320), level, dtype=np.uint8)
    x0 = max(0, min(320 - block_size, x))
    frame[10 : 10 + block_size, x0 : x0 + block_size] = block_level
    return frame


def _cfg():
    """Fake config with the attrs MotionRunner reads."""
    c = MagicMock()
    c.server_ip = "192.0.2.100"
    c.camera_id = "cam-001"
    c.certs_dir = "/nonexistent"
    return c


def _pairing():
    p = MagicMock()
    p.get_pairing_secret.return_value = "deadbeef" * 8
    return p


def _motion_cfg():
    # min_event_duration_seconds=0 so the detector fires start immediately
    # after min_start_frames — frames arrive faster than wall-clock in a
    # test, and we don't want to block on real time.
    return MotionConfig(
        background_alpha=0.1,
        pixel_diff_threshold=20,
        start_score_threshold=0.02,
        end_score_threshold=0.005,
        min_start_frames=3,
        min_end_frames=5,
        min_event_duration_seconds=0,
    )


class TestEmission:
    def test_start_and_end_emitted_in_order(self):
        poster = _FakePoster()
        frames = (
            [_blank() for _ in range(5)]
            + [_moving(40 + i * 10) for i in range(10)]
            + [_blank() for _ in range(10)]
        )

        def reader():
            yield from frames

        runner = MotionRunner(
            config=_cfg(),
            pairing_manager=_pairing(),
            motion_config=_motion_cfg(),
            poster_factory=lambda *a, **kw: poster,
            frame_reader=reader,
        )
        runner.start()
        # Wait for the generator to drain; stop only if still running.
        if runner._thread is not None:
            runner._thread.join(timeout=5)
        runner.stop()

        phases = [c["phase"] for c in poster.calls]
        assert phases == ["start", "end"]

        start_call = poster.calls[0]
        end_call = poster.calls[1]
        # Same event_id across start + end — server upserts by id.
        assert start_call["event_id"] == end_call["event_id"]
        assert start_call["event_id"].startswith("mot-")
        assert "cam-001" in start_call["event_id"]
        assert start_call["peak_score"] > 0
        assert end_call["duration_seconds"] > 0

    def test_no_motion_no_events(self):
        poster = _FakePoster()

        def reader():
            for _ in range(20):
                yield _blank()

        runner = MotionRunner(
            config=_cfg(),
            pairing_manager=_pairing(),
            motion_config=_motion_cfg(),
            poster_factory=lambda *a, **kw: poster,
            frame_reader=reader,
        )
        runner.start()
        # Wait for the generator to drain; stop only if still running.
        if runner._thread is not None:
            runner._thread.join(timeout=5)
        runner.stop()

        assert poster.calls == []

    def test_event_id_shape(self):
        poster = _FakePoster()
        frames = (
            [_blank()] * 5 + [_moving(40 + i * 10) for i in range(10)] + [_blank()] * 10
        )

        def reader():
            yield from frames

        cfg = _cfg()
        cfg.camera_id = "cam-d8ee"
        runner = MotionRunner(
            config=cfg,
            pairing_manager=_pairing(),
            motion_config=_motion_cfg(),
            poster_factory=lambda *a, **kw: poster,
            frame_reader=reader,
        )
        runner.start()
        # Wait for the generator to drain; stop only if still running.
        if runner._thread is not None:
            runner._thread.join(timeout=5)
        runner.stop()

        assert len(poster.calls) >= 1
        evt_id = poster.calls[0]["event_id"]
        # Format: mot-<YYYYMMDDTHHMMSSZ>-cam-d8ee-<uuid-prefix>
        parts = evt_id.split("-")
        assert parts[0] == "mot"
        # Date component is ISO8601 compact with Z suffix.
        assert parts[1].endswith("Z")
        assert "cam-d8ee" in evt_id


class TestFdReadPath:
    """Exercise the real fd-reader — pipe a deterministic byte stream
    through os.pipe(), assert frames are reassembled correctly."""

    def test_fd_reads_assemble_full_frames(self, tmp_path):
        import os

        from camera_streamer.motion_runner import FRAME_BYTES

        read_fd, write_fd = os.pipe()

        poster = _FakePoster()
        runner = MotionRunner(
            config=_cfg(),
            pairing_manager=_pairing(),
            frame_fd=read_fd,
            motion_config=_motion_cfg(),
            poster_factory=lambda *a, **kw: poster,
        )
        runner.start()

        # Write 5 blank frames then 10 motion frames then 10 blank,
        # all via the write fd, then close it so reader gets EOF.
        blank_bytes = _blank().tobytes()
        assert len(blank_bytes) == FRAME_BYTES

        for _ in range(5):
            os.write(write_fd, blank_bytes)
        # Three-frame differencing needs the block to MOVE between frames,
        # so use a column-advancing position per frame.
        for i in range(10):
            os.write(write_fd, _moving(40 + i * 10).tobytes())
        for _ in range(10):
            os.write(write_fd, blank_bytes)
        os.close(write_fd)

        # Give the reader thread up to 5 s to drain + stop on EOF.
        if runner._thread is not None:
            runner._thread.join(timeout=5)
        runner.stop()

        phases = [c["phase"] for c in poster.calls]
        assert phases == ["start", "end"]


class TestPosterSignatureHeaders:
    """Spot-check that the real MotionEventPoster builds valid headers."""

    def test_poster_builds_hmac_headers(self, monkeypatch):
        from camera_streamer.motion_runner import MotionEventPoster

        # Capture the request URLopen is called with.
        captured = {}

        class _FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return b""

        def fake_urlopen(req, context=None, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = req.data
            return _FakeResp()

        monkeypatch.setattr(
            "camera_streamer.motion_runner.urllib.request.urlopen",
            fake_urlopen,
        )

        poster = MotionEventPoster(_cfg(), _pairing())
        ok = poster.post(
            phase="start",
            event_id="mot-test-001",
            peak_score=0.12,
            peak_pixels_changed=1500,
            duration_seconds=0.0,
            started_at_epoch=1776620000.0,
        )
        assert ok
        # URL correct
        assert captured["url"] == ("https://192.0.2.100/api/v1/cameras/motion-event")
        # HMAC headers present — urllib normalises to "X-Camera-Id" (title-case)
        keys_lower = {k.lower() for k in captured["headers"]}
        assert "x-camera-id" in keys_lower
        assert "x-timestamp" in keys_lower
        assert "x-signature" in keys_lower
        # Body has the shape we expect.
        import json as _json

        body = _json.loads(captured["body"])
        assert body["phase"] == "start"
        assert body["event_id"] == "mot-test-001"
        assert body["peak_score"] == 0.12


class TestSensitivityMapping:
    """The 1-10 sensitivity dial maps to MotionConfig thresholds.
    Monotonic + clamped + sensible anchor at 5 (shipping default)."""

    def test_mapping_is_monotonic_in_sensitivity(self):
        from camera_streamer.motion_runner import motion_config_from_sensitivity

        prev = None
        for s in range(1, 11):
            cfg = motion_config_from_sensitivity(s)
            # Higher sensitivity => lower start threshold (easier to fire).
            if prev is not None:
                assert cfg.start_score_threshold <= prev.start_score_threshold
                assert cfg.pixel_diff_threshold <= prev.pixel_diff_threshold
            # end < start (hysteresis always maintained).
            assert cfg.end_score_threshold < cfg.start_score_threshold
            prev = cfg

    def test_default_is_sensitivity_5(self):
        from camera_streamer.motion_runner import motion_config_from_sensitivity

        # Medium (5) should match the shipping MotionConfig defaults so a
        # brand-new camera with no override sees the same behaviour this
        # test suite is tuned for.
        cfg = motion_config_from_sensitivity(5)
        assert cfg.pixel_diff_threshold == 8
        assert cfg.start_score_threshold == 0.006

    def test_values_outside_1_10_are_clamped(self):
        from camera_streamer.motion_runner import motion_config_from_sensitivity

        assert (
            motion_config_from_sensitivity(0).start_score_threshold
            == motion_config_from_sensitivity(1).start_score_threshold
        )
        assert (
            motion_config_from_sensitivity(99).start_score_threshold
            == motion_config_from_sensitivity(10).start_score_threshold
        )
