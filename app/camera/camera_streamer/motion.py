"""
On-camera motion detector — standard two-frame differencing with hysteresis.

Runs alongside the main H.264 encoder whenever the camera is paired.
Consumes a small grayscale (Y-plane) stream from the capture backend at
~5 fps and emits start/end events when movement crosses configurable
thresholds.

Algorithm: per-frame temporal differencing — each frame is compared to
the immediately preceding frame, NOT to a learned background model.
This is the classic approach used by the ``motion`` package, most
entry-level IP cameras, and the OpenCV "basic motion detection"
tutorials. Properties that matter:

  * No stuck-background failure mode. The "reference" resets every
    frame, so there's nothing to get wedged if the scene was captured
    during movement or the camera got knocked.
  * Adapts to lighting instantly — a slow sunset won't trigger
    because inter-frame deltas are tiny.
  * A subject that stops moving stops producing score. That's the
    desired behaviour for a "something moved" alert (vs "something is
    different from ten minutes ago" — different product).
  * Two-frame variant rejects single-frame sensor noise spikes: we
    only count a pixel as motion if it differs from BOTH the previous
    frame AND the one before that (classic three-frame intersection).

Runs in ~2 ms per frame on one Cortex-A53 core. numpy only, no opencv.

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

    # Per-pixel luminance difference (0-255) between consecutive frames
    # that counts as "changed". 8 works for indoor scenes at normal
    # webcam distance. Drop to 5 for dim scenes, raise to 15 if the
    # sensor is noisy. Exposed via Camera Settings → Recording slider.
    pixel_diff_threshold: int = 8

    # Fraction of pixels that must move frame-to-frame to count as
    # motion onset / exit. Hysteresis: start high, end low.
    # 0.005 = 384 px in a 320x240 frame ~ hand-sized at a few metres.
    start_score_threshold: float = 0.004
    end_score_threshold: float = 0.0015

    # Consecutive frames above / below the threshold required to
    # transition. At 5 fps: start=2 frames = 0.4 s, end=10 frames = 2 s.
    # Short start-hysteresis keeps the "I walked past the camera"
    # latency under half a second; the end-hysteresis dominates how
    # long "motion-ending" idle bursts are tolerated mid-event.
    min_start_frames: int = 2
    min_end_frames: int = 10

    # --- Legacy knobs, retained for API compatibility -----------------
    # The EMA background + stuck-reset machinery is gone (frame
    # differencing doesn't need them). These fields are accepted and
    # silently ignored so existing callers / configs don't break.
    background_alpha: float = 0.1
    stuck_reset_seconds: float = 60.0

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
        # Three-frame intersection rejects single-frame sensor noise
        # spikes: we hold the two most-recent frames and compare the
        # new one against both.
        self._prev1: np.ndarray | None = None  # the last frame we saw
        self._prev2: np.ndarray | None = None  # and the one before that
        self._frames_above = 0
        self._frames_below = 0
        self._current: MotionEvent | None = None
        self._pending_transition: tuple[str, MotionEvent] | None = None
        # True once the "start" transition for the current event has been
        # emitted — prevents repeated start emissions while in-event.
        # Reset to False when the event ends.
        self._start_emitted: bool = False

    @property
    def config(self) -> MotionConfig:
        return self._cfg

    @property
    def in_event(self) -> bool:
        """True while a motion event is currently active."""
        return self._current is not None

    def reset(self) -> None:
        """Drop frame history + in-flight event state. Used after stream restarts."""
        self._prev1 = None
        self._prev2 = None
        self._frames_above = 0
        self._frames_below = 0
        self._current = None
        self._pending_transition = None
        self._start_emitted = False

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

        # int16 diff keeps full precision without allocating float buffers
        # the size of the frame on every tick.
        frame_i = frame.astype(np.int16)

        # Warm-up: need two prior frames to form the 3-frame intersection.
        if self._prev1 is None:
            self._prev1 = frame_i
            return
        if self._prev2 is None:
            self._prev2 = self._prev1
            self._prev1 = frame_i
            return

        thr = self._cfg.pixel_diff_threshold

        # Classic two-frame absolute differencing (the approach every
        # entry-level IP camera and the OpenCV "basic motion detection"
        # tutorial uses): pixels that differ from the previous frame by
        # more than pixel_diff_threshold are "moving". Noise rejection
        # is handled downstream by ``min_start_frames`` hysteresis — a
        # single-frame salt-and-pepper spike can't satisfy "N frames
        # above threshold in a row".
        #
        # We keep a second previous frame (self._prev2) around because
        # process_frame also runs the ring-shift for it; a follow-up
        # change can trivially swap this to a 3-frame intersection if
        # noise becomes a problem in specific scenes.
        d1 = np.abs(frame_i - self._prev1) > thr
        changed_mask = d1
        changed_pixels = int(changed_mask.sum())
        score = changed_pixels / frame.size

        now = self._clock()
        self._update_hysteresis(score, changed_pixels, now)

        # Advance the ring: prev2 <- prev1, prev1 <- this.
        self._prev2 = self._prev1
        self._prev1 = frame_i

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
        """Emit the pending ``start`` if the event has outlived the min-duration.

        Idempotent: once the start has been emitted for the current event,
        further frames don't re-fire it. `_start_emitted` is cleared when
        the event closes (see ``_close_event``) or on ``reset()``.
        """
        evt = self._current
        if evt is None:
            return
        if self._start_emitted:
            return  # already emitted for this event
        if self._pending_transition is not None:
            return  # already queued for the poller
        age = now - evt.started_at
        if age >= self._cfg.min_event_duration_seconds:
            self._pending_transition = ("start", evt)
            self._start_emitted = True

    def _close_event(self, now: float) -> None:
        evt = self._current
        self._current = None
        self._frames_below = 0
        self._frames_above = 0
        start_was_emitted = self._start_emitted
        self._start_emitted = False
        if evt is None:
            return
        duration = now - evt.started_at
        if duration < self._cfg.min_event_duration_seconds or not start_was_emitted:
            # Event was too brief to emit a start — drop silently so the
            # server never sees a phantom event. The next transition
            # becomes a fresh onset.
            return
        evt.ended_at = now
        self._pending_transition = ("end", evt)
