# REQ: SWR-012, SWR-014; RISK: RISK-001, RISK-005; TEST: TC-019
"""Unit tests for PicameraH264Backend pre-roll plumbing."""

from __future__ import annotations

import io
import sys
import types
from types import SimpleNamespace

import pytest

from camera_streamer.picam_backend import MIN_RETAIN_BYTES, PicameraH264Backend


class FakeFileOutput:
    def __init__(self, sink):
        self.sink = sink


class FakeCircularOutput:
    def __init__(self, buffersize):
        self.buffersize = buffersize
        self.fileoutput = None
        self.buffered_chunks: list[bytes] = []
        self.started = False
        self.stopped = False

    def start(self):
        assert self.fileoutput is not None
        self.started = True
        for chunk in self.buffered_chunks:
            self.fileoutput.write(chunk)
        self.fileoutput.flush()

    def stop(self):
        self.stopped = True

    def emit_live(self, chunk: bytes):
        assert self.fileoutput is not None
        self.fileoutput.write(chunk)
        self.fileoutput.flush()


class FakeEncoder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.output = None


class FakePicam2:
    def __init__(self):
        self.recording_args = None

    def start_recording(self, encoder, output):
        self.recording_args = (encoder, output)
        encoder.output = output


@pytest.fixture
def fake_picamera2(monkeypatch):
    picamera2_mod = types.ModuleType("picamera2")
    encoders_mod = types.ModuleType("picamera2.encoders")
    outputs_mod = types.ModuleType("picamera2.outputs")
    encoders_mod.H264Encoder = FakeEncoder
    outputs_mod.FileOutput = FakeFileOutput
    outputs_mod.CircularOutput = FakeCircularOutput
    picamera2_mod.encoders = encoders_mod
    picamera2_mod.outputs = outputs_mod
    monkeypatch.setitem(sys.modules, "picamera2", picamera2_mod)
    monkeypatch.setitem(sys.modules, "picamera2.encoders", encoders_mod)
    monkeypatch.setitem(sys.modules, "picamera2.outputs", outputs_mod)


def _backend(config, motion_enabled=True):
    backend = PicameraH264Backend(config, motion_enabled=motion_enabled)
    backend._picam2 = FakePicam2()
    backend._ffmpeg = SimpleNamespace(stdin=io.BytesIO(), pid=4321)
    return backend


class TestStartEncoder:
    def test_attaches_circular_output_when_motion_pre_roll_enabled(
        self, fake_picamera2, camera_config, monkeypatch
    ):
        camera_config.update(MOTION_PREROLL_ENABLED="true", MOTION_PREROLL_SECONDS="3")
        backend = _backend(camera_config, motion_enabled=True)
        monkeypatch.setattr(
            "camera_streamer.picam_backend.time.monotonic", lambda: 10.0
        )

        backend._start_encoder()

        assert isinstance(backend._encoder.output, list)
        live_output, ring = backend._encoder.output
        assert isinstance(live_output, FakeFileOutput)
        assert isinstance(ring, FakeCircularOutput)
        assert (
            ring.buffersize == camera_config.motion_pre_roll_seconds * camera_config.fps
        )
        assert backend._pre_roll_output is ring

    def test_leaves_live_output_unchanged_when_pre_roll_disabled(
        self, fake_picamera2, camera_config
    ):
        camera_config.update(MOTION_PREROLL_ENABLED="false")
        backend = _backend(camera_config, motion_enabled=True)

        backend._start_encoder()

        assert isinstance(backend._encoder.output, FakeFileOutput)
        assert backend._pre_roll_output is None


class TestPreRollRecording:
    def test_start_and_stop_finalize_buffered_then_live_bytes(
        self, fake_picamera2, camera_config, monkeypatch, tmp_path
    ):
        camera_config.update(MOTION_PREROLL_ENABLED="true", MOTION_PREROLL_SECONDS="3")
        times = iter([100.0, 101.5, 103.0])
        monkeypatch.setattr(
            "camera_streamer.picam_backend.time.monotonic", lambda: next(times)
        )
        backend = _backend(camera_config, motion_enabled=True)
        backend._start_encoder()
        ring = backend._pre_roll_output
        ring.buffered_chunks = [b"pre-", b"roll-"]
        target = tmp_path / "event.mp4"

        actual = backend.start_pre_rolled_recording(
            str(target), started_at=1770000000.0
        )
        ring.emit_live(b"live")
        result = backend.stop_pre_rolled_recording("post_roll_done")

        assert actual == pytest.approx(1.5)
        assert result is not None
        assert result["path"] == str(target)
        assert result["pre_roll_seconds"] == pytest.approx(1.5)
        assert result["total_seconds"] == pytest.approx(3.0)
        assert target.read_bytes() == b"pre-roll-live"
        assert not (tmp_path / "event.mp4.part").exists()
        assert ring.started is True
        assert ring.stopped is True
        assert backend._pre_roll_file is None
        assert backend._pre_roll_part_path is None
        assert backend._pre_roll_final_path is None

    def test_aborted_small_recording_is_deleted(
        self, fake_picamera2, camera_config, monkeypatch, tmp_path
    ):
        camera_config.update(MOTION_PREROLL_ENABLED="true", MOTION_PREROLL_SECONDS="3")
        times = iter([200.0, 201.0, 202.0])
        monkeypatch.setattr(
            "camera_streamer.picam_backend.time.monotonic", lambda: next(times)
        )
        backend = _backend(camera_config, motion_enabled=True)
        backend._start_encoder()
        ring = backend._pre_roll_output
        ring.buffered_chunks = [b"x" * (MIN_RETAIN_BYTES // 4)]
        target = tmp_path / "aborted.mp4"

        backend.start_pre_rolled_recording(str(target), started_at=1770000100.0)
        result = backend.stop_pre_rolled_recording("aborted")

        assert result is None
        assert not target.exists()
        assert not (tmp_path / "aborted.mp4.part").exists()

    def test_state_resets_between_recording_cycles(
        self, fake_picamera2, camera_config, monkeypatch, tmp_path
    ):
        camera_config.update(MOTION_PREROLL_ENABLED="true", MOTION_PREROLL_SECONDS="2")
        times = iter([10.0, 11.0, 12.0, 20.0, 21.0, 22.0])
        monkeypatch.setattr(
            "camera_streamer.picam_backend.time.monotonic", lambda: next(times)
        )
        backend = _backend(camera_config, motion_enabled=True)
        backend._start_encoder()
        ring = backend._pre_roll_output

        first = tmp_path / "first.mp4"
        ring.buffered_chunks = [b"one-"]
        backend.start_pre_rolled_recording(str(first), started_at=1)
        ring.emit_live(b"a")
        backend.stop_pre_rolled_recording("post_roll_done")

        second = tmp_path / "second.mp4"
        ring.buffered_chunks = [b"two-"]
        backend.start_pre_rolled_recording(str(second), started_at=2)
        ring.emit_live(b"b")
        backend.stop_pre_rolled_recording("post_roll_done")

        assert first.read_bytes() == b"one-a"
        assert second.read_bytes() == b"two-b"
