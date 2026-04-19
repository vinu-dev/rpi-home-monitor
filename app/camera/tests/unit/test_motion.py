"""Unit tests for the MotionDetector.

Frames are fabricated deterministically so every transition is exactly
reproducible: a uniform-grey baseline with a synthetic "moving" rectangle
switched on and off by the test.
"""

from __future__ import annotations

import numpy as np
import pytest

from camera_streamer.motion import MotionConfig, MotionDetector, MotionEvent


@pytest.fixture
def clock():
    """Deterministic clock — each call advances by 0.2 s (5 fps)."""

    class _Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            self.t += 0.2
            return self.t

        def peek(self):
            return self.t

    return _Clock()


@pytest.fixture
def detector(clock):
    """Default detector wired to the deterministic clock."""
    cfg = MotionConfig(
        background_alpha=0.1,
        pixel_diff_threshold=20,
        start_score_threshold=0.02,
        end_score_threshold=0.005,
        min_start_frames=3,
        min_end_frames=5,
        min_event_duration_seconds=0.4,
    )
    return MotionDetector(config=cfg, clock=clock)


def _blank_frame(h=240, w=320, level=128):
    """A uniform grayscale frame at the given intensity."""
    return np.full((h, w), level, dtype=np.uint8)


def _frame_with_block(h=240, w=320, level=128, block_level=255, block_size=80):
    """A uniform frame with a bright square in the upper-left — 'motion'."""
    frame = np.full((h, w), level, dtype=np.uint8)
    frame[10 : 10 + block_size, 10 : 10 + block_size] = block_level
    return frame


class TestInputValidation:
    def test_rejects_non_2d_frame(self, detector):
        with pytest.raises(ValueError, match="2-D grayscale"):
            detector.process_frame(np.zeros((240, 320, 3), dtype=np.uint8))

    def test_rejects_non_uint8(self, detector):
        with pytest.raises(ValueError, match="uint8"):
            detector.process_frame(np.zeros((240, 320), dtype=np.float32))


class TestBackgroundSeed:
    def test_first_frame_seeds_background(self, detector):
        detector.process_frame(_blank_frame())
        assert not detector.in_event
        assert detector.poll_event() is None

    def test_identical_frames_never_trigger(self, detector):
        for _ in range(20):
            detector.process_frame(_blank_frame())
        assert not detector.in_event
        assert detector.poll_event() is None


class TestMotionOnsetAndEnd:
    def test_block_appears_then_disappears_emits_start_and_end(self, detector):
        # Seed baseline.
        for _ in range(5):
            detector.process_frame(_blank_frame())
        assert not detector.in_event

        # Motion ON — enough frames to satisfy min_start_frames AND
        # min_event_duration.
        for _ in range(10):
            detector.process_frame(_frame_with_block())

        start = detector.poll_event()
        assert start is not None
        phase, evt = start
        assert phase == "start"
        assert isinstance(evt, MotionEvent)
        assert evt.peak_score > 0.02
        assert evt.peak_pixels_changed > 0
        assert detector.in_event

        # Motion OFF — block removed, go back to baseline.
        for _ in range(10):
            detector.process_frame(_blank_frame())

        end = detector.poll_event()
        assert end is not None
        phase, evt = end
        assert phase == "end"
        assert evt.ended_at is not None
        assert evt.ended_at > evt.started_at
        assert not detector.in_event

    def test_brief_flicker_does_not_emit_start(self, detector):
        # Seed baseline.
        for _ in range(5):
            detector.process_frame(_blank_frame())

        # One frame of motion — below min_start_frames (3).
        detector.process_frame(_frame_with_block())
        # Back to baseline.
        for _ in range(5):
            detector.process_frame(_blank_frame())

        assert detector.poll_event() is None
        assert not detector.in_event

    def test_sub_minimum_duration_event_is_dropped(self, clock):
        """Event fires min_start_frames but ends before min_event_duration."""
        cfg = MotionConfig(
            background_alpha=0.1,
            pixel_diff_threshold=20,
            start_score_threshold=0.02,
            end_score_threshold=0.005,
            min_start_frames=3,
            min_end_frames=2,
            min_event_duration_seconds=5.0,  # very long — no event can
            # survive long enough to emit
        )
        detector = MotionDetector(config=cfg, clock=clock)
        for _ in range(5):
            detector.process_frame(_blank_frame())

        # 3 on-frames is just enough to flip into an event internally,
        # but the tight end-hysteresis + long min_event_duration means
        # the event dies silently.
        for _ in range(3):
            detector.process_frame(_frame_with_block())
        for _ in range(5):
            detector.process_frame(_blank_frame())

        # No start and no end should ever surface.
        assert detector.poll_event() is None
        assert not detector.in_event


class TestPollEventIdempotent:
    def test_poll_returns_transition_exactly_once(self, detector):
        for _ in range(5):
            detector.process_frame(_blank_frame())
        for _ in range(10):
            detector.process_frame(_frame_with_block())

        first = detector.poll_event()
        assert first is not None
        assert first[0] == "start"

        # Subsequent poll with no new transition.
        assert detector.poll_event() is None


class TestPeakTracking:
    def test_peak_score_records_highest_seen(self, detector):
        for _ in range(5):
            detector.process_frame(_blank_frame())

        # Small block — low peak.
        for _ in range(5):
            detector.process_frame(_frame_with_block(block_size=30))

        # Bigger block — higher peak.
        for _ in range(5):
            detector.process_frame(_frame_with_block(block_size=120))

        start = detector.poll_event()
        assert start is not None
        _, evt = start
        # peak_score is captured at start emission — should reflect the
        # highest score seen across all frames while in the event.
        assert evt.peak_score >= 0.02


class TestReset:
    def test_reset_drops_background_and_state(self, detector):
        for _ in range(5):
            detector.process_frame(_blank_frame())
        for _ in range(10):
            detector.process_frame(_frame_with_block())
        assert detector.in_event

        detector.reset()
        assert not detector.in_event
        assert detector.poll_event() is None

        # After reset, the next frame is treated as a new background seed.
        detector.process_frame(_blank_frame())
        detector.process_frame(_blank_frame())
        assert not detector.in_event


class TestBackgroundAdaption:
    def test_gradual_lighting_drift_does_not_trigger(self, detector):
        """Slowly brightening room should be absorbed by the EMA, not flagged."""
        # Seed.
        detector.process_frame(_blank_frame(level=100))

        # Raise the baseline by 1 luminance step per frame — well below
        # pixel_diff_threshold.
        for step in range(30):
            detector.process_frame(_blank_frame(level=100 + step))

        assert not detector.in_event
        assert detector.poll_event() is None

    def test_sudden_jump_does_trigger(self, detector):
        """Sudden floodlight / flashlight sweep — above threshold, should fire."""
        for _ in range(5):
            detector.process_frame(_blank_frame(level=80))

        # Jump the entire frame by 60 luminance — every pixel changes.
        for _ in range(5):
            detector.process_frame(_blank_frame(level=140))

        # Motion detected.
        assert detector.in_event
        start = detector.poll_event()
        assert start is not None
        assert start[0] == "start"
        _, evt = start
        # Near-total-frame change — peak_score approaches 1.0.
        assert evt.peak_score > 0.9


class TestConfigDefaults:
    def test_default_config_values_sensible(self):
        cfg = MotionConfig()
        assert 0 < cfg.background_alpha < 1
        assert 0 < cfg.pixel_diff_threshold < 256
        assert cfg.start_score_threshold > cfg.end_score_threshold
        assert cfg.min_start_frames >= 1
        assert cfg.min_end_frames >= cfg.min_start_frames
        assert cfg.min_event_duration_seconds > 0
