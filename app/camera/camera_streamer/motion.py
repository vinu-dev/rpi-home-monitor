"""
On-camera motion detector — grayscale frame differencing with hysteresis.

Runs alongside the main H.264 encoder whenever the camera is paired.
Consumes a small grayscale (Y-plane) stream from the capture backend at
~5 fps and emits start/end events when movement crosses configurable
thresholds.

Algorithm: exponential-moving-average background + absolute difference
+ threshold + hysteresis. Deliberately simple so it fits in ~3 ms per
frame on one Cortex-A53 core and requires no opencv dependency.

See `docs/exec-plans/motion-detection.md` for the design rationale.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("camera-streamer.motion")


@dataclass
class MotionConfig:
    """Tunable detector parameters.

    Defaults target the ZeroCam (OV5647) at 320x240 grayscale / 5 fps.
    """

    # EMA background learning rate. Higher = adapts faster to lighting
    # drift but also "learns" a stationary person into the background.
    background_alpha: float = 0.1

    # Per-pixel luminance difference considered "changed" (0-255).
    pixel_diff_threshold: int = 20

    # Fraction of pixels changed that counts as motion onset / exit.
    # Hysteresis: start uses the higher threshold, end uses the lower.
    start_score_threshold: float = 0.02
    end_score_threshold: float = 0.005

    # Consecutive frames above / below the threshold required to
    # transition. At 5 fps: start=3 frames = 0.6 s, end=15 frames = 3 s.
    min_start_frames: int = 3
    min_end_frames: int = 15

    # Absolute minimum wall-time an event must last before `phase=start`
    # is reported. Kills sub-second flashes from bugs / reflections.
    min_event_duration_seconds: float = 0.8


@dataclass
class MotionEvent:
    """An in-progress or finalised motion event, camera-side.

    Server-side representation is richer (adds clip_ref, rate-limit
    metadata). See `monitor.models.MotionEvent`.
    """

    started_at: float  # epoch seconds
    ended_at: float | None = None
    peak_score: float = 0.0
    peak_pixels_changed: int = 0
    frames_above: int = 0
    frames_below: int = 0

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)


class MotionDetector:
    """Stateful per-camera motion detector.

    Feed grayscale frames via ``process_frame``; poll state transitions
    via ``poll_event``. Thread-safe for single-producer single-consumer
    (the capture thread produces frames, the event-emitter thread
    consumes transitions).

    Args:
        config: Tuning parameters. Defaults are sensible for 320x240/5fps.
        clock: Injectable time source for deterministic tests.
    """

    def __init__(self, config: MotionConfig | None = None, clock=None):
        self._cfg = config or MotionConfig()
        self._clock = clock or time.time
        self._background: np.ndarray | None = None
        self._frames_above = 0
        self._frames_below = 0
        self._current: MotionEvent | None = None
        self._pending_transition: tuple[str, MotionEvent] | None = None

    @property
    def config(self) -> MotionConfig:
        return self._cfg

    @property
    def in_event(self) -> bool:
        """True while a motion event is currently active."""
        return self._current is not None

    def reset(self) -> None:
        """Drop background + in-flight event state. Used after stream restarts."""
        self._background = None
        self._frames_above = 0
        self._frames_below = 0
        self._current = None
        self._pending_transition = None

    def process_frame(self, frame: np.ndarray) -> None:
        """Feed one grayscale frame into the detector.

        Args:
            frame: 2-D uint8 ndarray. Shape (H, W). Larger frames are
                centre-cropped to ``self._cfg`` target size — we don't
                resample here because the capture backend already hands
                us the lores stream at the desired resolution.
        """
        if frame.ndim != 2:
            raise ValueError(
                f"MotionDetector expects 2-D grayscale, got ndim={frame.ndim}"
            )
        if frame.dtype != np.uint8:
            raise ValueError(f"MotionDetector expects uint8, got {frame.dtype}")

        # Cheap float conversion only where needed (float32 for EMA math).
        frame_f = frame.astype(np.float32)

        if self._background is None:
            # First frame — seed the background and bail; no diff yet.
            self._background = frame_f.copy()
            return

        # EMA update happens AFTER diff so a real event doesn't instantly
        # dissolve into the background.
        diff = np.abs(frame_f - self._background)
        changed_mask = diff > self._cfg.pixel_diff_threshold
        changed_pixels = int(changed_mask.sum())
        score = changed_pixels / frame.size

        now = self._clock()
        self._update_hysteresis(score, changed_pixels, now)

        alpha = self._cfg.background_alpha
        # Only adapt where the pixel did NOT change — stationary regions
        # track lighting drift, moving regions are left alone so we don't
        # "learn" a walking person into the model.
        stationary = ~changed_mask
        self._background[stationary] = (1.0 - alpha) * self._background[
            stationary
        ] + alpha * frame_f[stationary]

    def poll_event(self) -> tuple[str, MotionEvent] | None:
        """Return any pending state transition ("start" or "end"), or None.

        Called by the event-emitter thread after each ``process_frame``.
        Idempotent — returns the same transition exactly once, then None
        until the next transition.
        """
        transition = self._pending_transition
        self._pending_transition = None
        return transition

    # --- Internals --------------------------------------------------------

    def _update_hysteresis(self, score: float, changed_pixels: int, now: float) -> None:
        cfg = self._cfg

        if self._current is None:
            # Not in an event — look for an onset.
            if score >= cfg.start_score_threshold:
                self._frames_above += 1
                self._frames_below = 0
                if self._frames_above >= cfg.min_start_frames:
                    evt = MotionEvent(
                        started_at=now,
                        peak_score=score,
                        peak_pixels_changed=changed_pixels,
                        frames_above=self._frames_above,
                    )
                    self._current = evt
                    # Don't emit the start transition yet — the min-duration
                    # filter in poll-time would need a timer. Instead: hold
                    # the "start" until the event has existed for
                    # min_event_duration_seconds AND is still active. Events
                    # that die before that are dropped entirely.
                    self._frames_above = 0
            else:
                self._frames_above = 0

        else:
            # In an event — update peak, look for exit.
            evt = self._current
            if score > evt.peak_score:
                evt.peak_score = score
                evt.peak_pixels_changed = changed_pixels

            if score <= cfg.end_score_threshold:
                self._frames_below += 1
                self._frames_above = 0
                if self._frames_below >= cfg.min_end_frames:
                    self._close_event(now)
            else:
                self._frames_below = 0
                self._maybe_emit_start(now)

    def _maybe_emit_start(self, now: float) -> None:
        """Emit the pending ``start`` if the event has outlived the min-duration."""
        evt = self._current
        if evt is None:
            return
        if self._pending_transition is not None:
            return  # already queued
        age = now - evt.started_at
        if age >= self._cfg.min_event_duration_seconds:
            self._pending_transition = ("start", evt)

    def _close_event(self, now: float) -> None:
        evt = self._current
        self._current = None
        self._frames_below = 0
        self._frames_above = 0
        if evt is None:
            return
        duration = now - evt.started_at
        if duration < self._cfg.min_event_duration_seconds:
            # Event was too brief to emit a start — drop silently so the
            # server never sees a phantom event. The next transition
            # becomes a fresh onset.
            return
        evt.ended_at = now
        self._pending_transition = ("end", evt)
