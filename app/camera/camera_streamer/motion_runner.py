# REQ: SWR-014, SWR-008; RISK: RISK-005; TEST: TC-019
"""
On-camera motion-detector runtime — connects MotionDetector to the world.

Responsibilities:
- Reads raw grayscale frames from a pipe fed by ffmpeg's lores tee.
- Feeds each frame to a MotionDetector.
- On start/end transitions, POSTs an HMAC-signed motion event to the
  paired server (same scheme as heartbeat).

Lifecycle: started by StreamManager once streaming is active; stopped
cleanly when the pipeline tears down. Failures are logged and retried
via the outer service watchdog.

Design note — pipe vs FIFO: the runner reads from a Python-owned pipe
(``os.pipe()``) whose write end is passed to ffmpeg as a numbered fd
(``pipe:<N>``). This avoids the on-disk-FIFO dance entirely (no inode
in /tmp, no interaction with ffmpeg's ``-y``/refuse-overwrite logic,
no race with systemd's PrivateTmp cleanup).

See docs/archive/exec-plans/motion-detection.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable

import numpy as np

from camera_streamer.motion import MotionConfig, MotionDetector

log = logging.getLogger("camera-streamer.motion_runner")

# Lores stream geometry — must match the ffmpeg -vf scale= args in stream.py.
LORES_WIDTH = 320
LORES_HEIGHT = 240
FRAME_BYTES = LORES_WIDTH * LORES_HEIGHT  # gray8


def motion_config_from_sensitivity(sensitivity: int) -> MotionConfig:
    """Map the operator-facing 1-10 sensitivity dial to detector thresholds.

    Sensitivity is a single knob because operators shouldn't have to
    reason about per-pixel deltas or score fractions. The mapping is
    monotonic and roughly log-linear around the shipped default (5).

    Anchors chosen from on-device tuning at indoor light. The thresholds
    operate on the Y plane only, so they hold across every Pi camera
    sensor we ship; sensor-specific drift is a small offset that the
    1-10 dial covers.

    - **1-3 "Low":** reject ambient noise + small movement. Good outdoor
      default — ignores rain, fine wind sway, flies.
    - **4-6 "Medium" (default 5):** catches hand-sized motion at a few
      metres, rejects typical indoor sensor noise.
    - **7-10 "High":** picks up distant / slow motion. Will false-fire
      on fans, monitor flicker, and similar ambient scene noise.
    """
    s = max(1, min(10, int(sensitivity)))

    # pixel_diff_threshold: per-pixel 0-255 delta that counts as "changed".
    # Higher sensitivity => lower threshold => more pixels flagged.
    pixel_diff = {1: 20, 2: 15, 3: 12, 4: 10, 5: 8, 6: 6, 7: 5, 8: 4, 9: 3, 10: 3}[s]

    # Start / end score (fraction-of-frame). Hysteresis gap widens at lower
    # sensitivity for outdoor stability; narrows at higher sensitivity for
    # responsiveness.
    start = {
        1: 0.020,
        2: 0.015,
        3: 0.010,
        4: 0.008,
        5: 0.006,
        6: 0.004,
        7: 0.003,
        8: 0.002,
        9: 0.0015,
        10: 0.001,
    }[s]
    end = start / 3.0

    return MotionConfig(
        pixel_diff_threshold=pixel_diff,
        start_score_threshold=start,
        end_score_threshold=end,
    )


# HTTP post timeout (seconds). Motion events are time-sensitive but we
# should not block the detector thread forever.
POST_TIMEOUT = 10


def _build_signature(
    secret_hex: str, camera_id: str, timestamp: str, body_bytes: bytes
) -> str:
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    message = f"{camera_id}:{timestamp}:{body_hash}"
    return hmac.new(
        bytes.fromhex(secret_hex),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def _ssl_context(certs_dir: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    cert = os.path.join(certs_dir, "client.crt")
    key = os.path.join(certs_dir, "client.key")
    if os.path.isfile(cert) and os.path.isfile(key):
        ctx.load_cert_chain(cert, key)
    return ctx


class MotionEventPoster:
    """POSTs HMAC-signed motion events to the paired server.

    Shape matches the server's /api/v1/cameras/motion-event handler.
    Fire-and-forget: failures are logged and swallowed so they never
    block the detector thread.
    """

    def __init__(self, config, pairing_manager):
        self._config = config
        self._pairing = pairing_manager

    def post(
        self,
        phase: str,
        event_id: str,
        peak_score: float,
        peak_pixels_changed: int,
        duration_seconds: float,
        started_at_epoch: float,
    ) -> bool:
        server_ip = self._config.server_ip
        if not server_ip:
            log.debug("No server_ip — skipping motion event POST")
            return False
        secret = self._pairing.get_pairing_secret()
        if not secret:
            log.debug("No pairing secret — skipping motion event POST")
            return False

        camera_id = self._config.camera_id
        payload = {
            "phase": phase,
            "event_id": event_id,
            "started_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at_epoch)
            ),
            "peak_score": round(peak_score, 4),
            "peak_pixels_changed": peak_pixels_changed,
            "duration_seconds": round(duration_seconds, 2),
        }
        body = json.dumps(payload).encode()
        timestamp = str(int(time.time()))
        signature = _build_signature(secret, camera_id, timestamp, body)

        url = f"https://{server_ip}/api/v1/cameras/motion-event"
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Camera-ID": camera_id,
                "X-Timestamp": timestamp,
                "X-Signature": signature,
            },
        )
        try:
            ctx = _ssl_context(self._config.certs_dir)
            with urllib.request.urlopen(req, context=ctx, timeout=POST_TIMEOUT) as resp:
                log.info(
                    "motion event %s posted (event_id=%s, HTTP %d)",
                    phase,
                    event_id,
                    resp.status,
                )
                return True
        except urllib.error.HTTPError as e:
            # 429 is rate limiting — expected behaviour, not an error.
            if e.code == 429:
                log.info("motion event %s rate-limited by server", phase)
            else:
                log.warning(
                    "motion event %s rejected by server: HTTP %d", phase, e.code
                )
        except (urllib.error.URLError, OSError) as e:
            log.warning("motion event %s post failed: %s", phase, e)
        return False


class MotionRunner:
    """Background thread: read YUV frames, feed detector, emit events.

    Args:
        config: ConfigManager instance (server_ip, camera_id, certs_dir).
        pairing_manager: PairingManager instance (get_pairing_secret()).
        frame_fd: File descriptor for the read end of the pipe ffmpeg
            writes raw grayscale frames to. MotionRunner takes ownership
            — it will ``os.close()`` the fd on stop(). Ignored if
            ``frame_reader`` is supplied.
        motion_config: MotionConfig overrides (None → defaults).
        poster_factory: Optional override for the MotionEventPoster
            (used by tests to inject a fake).
        frame_reader: Optional override for frame reading. Callable
            returning a generator of 2-D uint8 ndarrays. Used by tests
            to sidestep real fd IO.
        passive: If True, the runner starts no thread; the caller is
            expected to push frames via ``process_frame(y_plane)``. This
            is the Picamera2-dual-stream path where the lores callback
            is already running on its own thread (picam_backend).
        warmup_seconds: Frames fed within this window after start() are
            discarded. Suppresses the phantom motion event caused by
            auto-exposure / auto-white-balance settling when the ISP
            pipeline restarts. Sensor-agnostic — the gate is a wall-clock
            timer, not a sensor-specific exposure metric. Defaults to 3 s,
            which empirically covers AE/AWB convergence on every Pi
            camera sensor we ship support for (OV5647, IMX219, IMX477,
            IMX708).
    """

    def __init__(
        self,
        config,
        pairing_manager,
        frame_fd: int | None = None,
        motion_config: MotionConfig | None = None,
        poster_factory: Callable | None = None,
        frame_reader: Callable | None = None,
        passive: bool = False,
        warmup_seconds: float = 3.0,
    ):
        if not passive and frame_fd is None and frame_reader is None:
            raise ValueError(
                "MotionRunner requires frame_fd, frame_reader, or passive=True"
            )
        self._config = config
        self._pairing = pairing_manager
        self._frame_fd = frame_fd
        self._detector = MotionDetector(motion_config)
        factory = poster_factory or MotionEventPoster
        self._poster = factory(config, pairing_manager)
        self._running = False
        self._thread: threading.Thread | None = None
        self._frame_reader = frame_reader
        self._passive = passive
        self._passive_lock = threading.Lock()
        self._warmup_seconds = warmup_seconds
        self._warmup_until: float = 0.0

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            log.debug("MotionRunner already running")
            return True
        self._running = True
        # Reset detector state from any prior run and set the warm-up gate.
        # The gate discards frames while the ISP's AE/AWB is converging so
        # the brightness surge on pipeline restart doesn't look like motion.
        self._detector.reset()
        self._warmup_until = time.monotonic() + self._warmup_seconds
        if self._passive:
            # No thread — frames arrive via process_frame() from the
            # Picamera2 lores callback.
            log.info(
                "MotionRunner started (passive mode, warmup=%.1fs)",
                self._warmup_seconds,
            )
            return True
        self._thread = threading.Thread(
            target=self._run, name="motion-runner", daemon=True
        )
        self._thread.start()
        log.info(
            "MotionRunner started (fd=%s, reader=%s, warmup=%.1fs)",
            self._frame_fd,
            "yes" if self._frame_reader else "no",
            self._warmup_seconds,
        )
        return True

    def process_frame(self, y_plane: np.ndarray) -> None:
        """Feed a single lores grayscale frame (passive mode).

        Safe to call from any thread; the detector + event emission
        are serialised by an internal lock so bursts of frames don't
        interleave half-updated state.
        """
        if not self._running:
            return
        if time.monotonic() < self._warmup_until:
            return
        with self._passive_lock:
            self._detector.process_frame(y_plane)
            transition = self._detector.poll_event()
            if transition is not None:
                self._emit_transition(transition)

    def stop(self) -> None:
        self._running = False
        # Close the read-fd so a blocked os.read in _read_fd_frames wakes.
        if self._frame_fd is not None:
            try:
                os.close(self._frame_fd)
            except OSError:
                pass
            self._frame_fd = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    # --- Internals --------------------------------------------------------

    def _run(self) -> None:
        reader = self._frame_reader or self._read_fd_frames
        for frame in reader():
            if not self._running:
                break
            if frame is None:
                continue
            if time.monotonic() < self._warmup_until:
                continue
            self._detector.process_frame(frame)
            transition = self._detector.poll_event()
            if transition is not None:
                self._emit_transition(transition)

    def _read_fd_frames(self):
        """Generator: yield fixed-size grayscale frames from the pipe fd."""
        if self._frame_fd is None:
            return
        buf = bytearray()
        while self._running:
            try:
                chunk = os.read(self._frame_fd, FRAME_BYTES - len(buf) or FRAME_BYTES)
            except OSError as exc:
                # EBADF happens when stop() closed the fd — exit cleanly.
                log.info("MotionRunner fd read stopped: %s", exc)
                return
            if not chunk:
                # Writer (ffmpeg) closed its end — pipeline torn down.
                log.info("MotionRunner fd EOF — exiting read loop")
                return
            buf.extend(chunk)
            while len(buf) >= FRAME_BYTES:
                frame_bytes = bytes(buf[:FRAME_BYTES])
                del buf[:FRAME_BYTES]
                yield np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                    LORES_HEIGHT, LORES_WIDTH
                )

    def _emit_transition(self, transition) -> None:
        phase, evt = transition
        # MotionDetector doesn't assign an id itself; we tag the start
        # event here and carry the same id through to the end so the
        # server upserts on a stable key.
        if phase == "start":
            evt.id = self._new_event_id(evt.started_at)  # type: ignore[attr-defined]
            event_id = evt.id
        else:
            event_id = getattr(evt, "id", self._new_event_id(evt.started_at))

        self._poster.post(
            phase=phase,
            event_id=event_id,
            peak_score=evt.peak_score,
            peak_pixels_changed=evt.peak_pixels_changed,
            duration_seconds=evt.duration_seconds,
            started_at_epoch=evt.started_at,
        )

    def _new_event_id(self, started_at_epoch: float) -> str:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(started_at_epoch))
        suffix = uuid.uuid4().hex[:8]
        return f"mot-{ts}-{self._config.camera_id}-{suffix}"
