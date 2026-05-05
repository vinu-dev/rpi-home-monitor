# REQ: SWR-012, SWR-014, SWR-053; RISK: RISK-001, RISK-005, RISK-007; TEST: TC-005, TC-012, TC-019
"""
Picamera2-based capture backend (ADR-0021 target).

Replaces the libcamera-vid subprocess + ffmpeg-tee approach with:

  Picamera2 pipeline                         ffmpeg subprocess
    │                                         │
    ├─ main 1920×1080 YUV ─► H264Encoder ───► stdin ──► RTSPS push
    │                                         (just -c copy, no decode)
    │
    └─ lores 320×240 YUV ─► Python callback
                             │
                             ▼
                         MotionDetector + HMAC poster

Why this is safe on a Zero 2W:
  - Lores frames come straight from the ISP — no decode, no scale filter.
  - The RTSP ffmpeg only copies bytes (no decoder in its process at all).
  - Dual-stream is a libcamera primitive — one sensor owner (Picamera2),
    two cheap downstream sinks.

Contrast with the prior ``stream.py`` design:
  - That one asked ffmpeg to decode 1080p H.264 + rescale to 320×240 for
    the motion pipe. ~54 % of a core, plus os.pipe backpressure that
    stalled the RTSP output and caused a 20-s live-feed delay.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable

log = logging.getLogger("camera-streamer.picam_backend")


_LIBCAMERA_ENUM_CODES = {
    # libcamera draft NoiseReductionModeEnum
    "NoiseReductionMode": {
        "Off": 0,
        "Fast": 1,
        "HighQuality": 2,
        "Minimal": 3,
        "ZSL": 4,
    },
    # libcamera AwbMode (auto-white-balance mode preset)
    "AwbMode": {
        "Auto": 0,
        "Tungsten": 1,
        "Fluorescent": 2,
        "Indoor": 3,
        "Daylight": 4,
        "Cloudy": 5,
        "Custom": 6,
    },
}


def _resolve_libcamera_enum(key: str, value):
    """Translate a string image_quality enum value into the libcamera form.

    Tries the live ``libcamera.controls`` Python bindings first (so we
    pass the actual enum instance the kernel expects). Falls back to
    the well-known integer codes when the bindings aren't available
    (test hosts) or when the enum's class moved between libcamera
    versions. Returns ``None`` to signal "drop this key" — the caller
    pops it from the controls dict so an invalid value never reaches
    ``Picamera2.set_controls``.
    """
    if not isinstance(value, str):
        return value if isinstance(value, int) else None
    # Live libcamera bindings (production path)
    try:
        from libcamera import controls as _libc_controls  # type: ignore

        if key == "NoiseReductionMode":
            enum_cls = _libc_controls.draft.NoiseReductionModeEnum
        elif key == "AwbMode":
            enum_cls = _libc_controls.AwbModeEnum
        else:
            enum_cls = None
        if enum_cls is not None:
            try:
                return getattr(enum_cls, value)
            except AttributeError:
                log.warning("libcamera enum %s has no %r — dropping", key, value)
                return None
    except (ImportError, AttributeError):
        pass
    # Fallback: well-known integer codes
    table = _LIBCAMERA_ENUM_CODES.get(key, {})
    if value in table:
        return table[value]
    log.warning("Unknown enum value for %s: %r — dropping", key, value)
    return None


MAIN_FORMAT = "YUV420"
LORES_FORMAT = "YUV420"

# Lores analysis geometry / cadence — matches the motion detector's
# expectations (320x240 grayscale, ~5 fps). If you change these, also
# update ``camera_streamer/motion.py`` defaults or inject a new
# ``MotionConfig``.
LORES_WIDTH = 320
LORES_HEIGHT = 240
LORES_FPS = 5
MIN_RETAIN_BYTES = 32 * 1024


class PicameraH264Backend:
    """Drive Picamera2 with dual-stream (main H.264 + lores YUV).

    Spawns ffmpeg to consume the H.264 byte stream on its stdin and push
    RTSPS to the server. Runs a lores-frame thread that calls ``frame_cb``
    with each 320×240 Y-plane at ~5 fps.

    This class is isolated in its own module so it's trivial to keep the
    non-Picamera-2 path (libcamera-vid CLI + ffmpeg) as a fallback — the
    two backends expose the same start/stop/is_streaming contract that
    ``StreamManager`` relies on.
    """

    def __init__(
        self,
        config,
        frame_cb: Callable | None = None,
        motion_enabled: bool = False,
    ):
        self._config = config
        self._frame_cb = frame_cb if motion_enabled else None
        self._motion_enabled = motion_enabled
        self._picam2 = None
        self._encoder = None
        self._ffmpeg = None
        self._lores_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._pre_roll_output = None
        self._pre_roll_buffer_started_monotonic: float | None = None
        self._pre_roll_file = None
        self._pre_roll_part_path: str | None = None
        self._pre_roll_final_path: str | None = None
        self._pre_roll_actual_seconds = 0.0
        self._pre_roll_recording_started_monotonic: float | None = None
        self._pre_roll_event_started_at = None

    # --- Public lifecycle (mirrors StreamManager's) ---------------------

    def start(self) -> bool:
        """Start Picamera2, the encoder, ffmpeg, and the lores loop."""
        if self._running:
            return True
        try:
            self._start_picam()
            self._start_ffmpeg()
            self._start_encoder()
            # Flip the running flag BEFORE spawning the lores thread — the
            # thread's loop guards on self._running and would otherwise race
            # into an immediate exit if it reaches the while-check before
            # we flip the flag.
            self._running = True
            if self._motion_enabled and self._frame_cb is not None:
                self._start_lores_thread()
            log.info("PicameraH264Backend started (motion=%s)", self._motion_enabled)
            return True
        except Exception:
            log.exception("Picamera backend start failed; tearing down")
            self.stop()
            return False

    def stop(self) -> None:
        """Tear everything down cleanly."""
        self._running = False
        try:
            self.stop_pre_rolled_recording("aborted")
        except Exception:  # pragma: no cover - defensive cleanup
            log.debug("pre-roll teardown failed", exc_info=True)
        # Encoder + picam first so frames stop flowing before we close ffmpeg's stdin.
        try:
            if self._picam2 is not None:
                try:
                    self._picam2.stop_recording()
                except Exception:  # pragma: no cover
                    log.debug("picam2.stop_recording failed", exc_info=True)
                try:
                    self._picam2.close()
                except Exception:  # pragma: no cover
                    log.debug("picam2.close failed", exc_info=True)
        finally:
            self._picam2 = None
            self._encoder = None

        if self._lores_thread is not None and self._lores_thread.is_alive():
            self._lores_thread.join(timeout=5)
        self._lores_thread = None

        if self._ffmpeg is not None:
            try:
                # Closing stdin lets ffmpeg finish its muxer cleanup.
                if self._ffmpeg.stdin and not self._ffmpeg.stdin.closed:
                    self._ffmpeg.stdin.close()
                self._ffmpeg.terminate()
                try:
                    self._ffmpeg.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._ffmpeg.kill()
                    self._ffmpeg.wait(timeout=2)
            except OSError:
                pass
            self._ffmpeg = None
        log.info("PicameraH264Backend stopped")

    @property
    def is_streaming(self) -> bool:
        if not self._running:
            return False
        if self._ffmpeg is None or self._ffmpeg.poll() is not None:
            return False
        return self._picam2 is not None

    def start_pre_rolled_recording(self, path: str, started_at) -> float:
        """Flush the motion pre-roll ring into ``path`` and keep writing live.

        Returns the buffered duration that was available when the motion event
        fired. When pre-roll is disabled, this becomes a no-op returning 0.
        """
        with self._lock:
            if self._pre_roll_output is None:
                return 0.0
            if self._pre_roll_file is not None:
                log.warning(
                    "start_pre_rolled_recording called while recording %s is active",
                    self._pre_roll_final_path,
                )
                return self._pre_roll_actual_seconds

            final_path = os.fspath(path)
            part_path = f"{final_path}.part"
            parent = os.path.dirname(final_path) or "."
            os.makedirs(parent, exist_ok=True)

            now = time.monotonic()
            actual_pre_roll = self._available_pre_roll_seconds(now)
            output_file = open(part_path, "wb")
            try:
                self._pre_roll_output.fileoutput = output_file
                self._pre_roll_output.start()
            except Exception:
                self._pre_roll_output.fileoutput = None
                output_file.close()
                try:
                    os.remove(part_path)
                except OSError:
                    pass
                raise

            self._pre_roll_file = output_file
            self._pre_roll_part_path = part_path
            self._pre_roll_final_path = final_path
            self._pre_roll_actual_seconds = actual_pre_roll
            self._pre_roll_recording_started_monotonic = now
            self._pre_roll_event_started_at = started_at
            log.info(
                "Started pre-roll recording %s (buffered %.2fs)",
                final_path,
                actual_pre_roll,
            )
            return actual_pre_roll

    def stop_pre_rolled_recording(self, reason: str):
        """Close an active pre-roll recording and optionally finalize it."""
        with self._lock:
            if (
                self._pre_roll_file is None
                or self._pre_roll_part_path is None
                or self._pre_roll_final_path is None
            ):
                return None

            output_file = self._pre_roll_file
            part_path = self._pre_roll_part_path
            final_path = self._pre_roll_final_path
            pre_roll_seconds = self._pre_roll_actual_seconds
            started_monotonic = self._pre_roll_recording_started_monotonic

            try:
                if self._pre_roll_output is not None:
                    self._pre_roll_output.stop()
                    self._pre_roll_output.fileoutput = None
            finally:
                output_file.close()

            total_seconds = pre_roll_seconds
            if started_monotonic is not None:
                total_seconds += max(0.0, time.monotonic() - started_monotonic)

            try:
                size_bytes = os.path.getsize(part_path)
            except OSError:
                size_bytes = 0

            result = None
            try:
                if reason == "aborted" and size_bytes < MIN_RETAIN_BYTES:
                    try:
                        os.remove(part_path)
                    except FileNotFoundError:
                        pass
                    log.info(
                        "Discarded small aborted pre-roll recording %s (%d bytes)",
                        part_path,
                        size_bytes,
                    )
                else:
                    os.replace(part_path, final_path)
                    result = {
                        "path": final_path,
                        "pre_roll_seconds": pre_roll_seconds,
                        "total_seconds": total_seconds,
                    }
                    log.info(
                        "Stopped pre-roll recording %s (%s, %.2fs total)",
                        final_path,
                        reason,
                        total_seconds,
                    )
                return result
            finally:
                self._reset_pre_roll_recording_state()

    # --- Internals ------------------------------------------------------

    def _start_picam(self) -> None:
        from picamera2 import Picamera2  # local import: optional dep

        cfg = self._config
        picam2 = Picamera2()

        main_stream = {"size": (cfg.width, cfg.height), "format": MAIN_FORMAT}
        controls = {"FrameRate": float(cfg.fps)}

        if self._motion_enabled:
            lores_stream = {
                "size": (LORES_WIDTH, LORES_HEIGHT),
                "format": LORES_FORMAT,
            }
            video_config = picam2.create_video_configuration(
                main=main_stream,
                lores=lores_stream,
                controls=controls,
            )
        else:
            # No lores — saves a bit of memory bandwidth when motion is off.
            video_config = picam2.create_video_configuration(
                main=main_stream,
                controls=controls,
            )

        picam2.configure(video_config)
        self._picam2 = picam2
        log.info(
            "Picamera2 configured: main=%dx%d @ %d fps, lores=%s",
            cfg.width,
            cfg.height,
            cfg.fps,
            f"{LORES_WIDTH}x{LORES_HEIGHT}" if self._motion_enabled else "off",
        )

    def _start_ffmpeg(self) -> None:
        """Spawn ffmpeg reading H.264 on stdin, pushing RTSPS out."""
        tls_flags = self._tls_flags()
        stream_url = self._stream_url()

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-use_wallclock_as_timestamps",
            "1",
            "-fflags",
            "+genpts",
            "-probesize",
            "5000000",
            "-analyzeduration",
            "5000000",
            "-f",
            "h264",
            "-i",
            "pipe:0",
            "-c:v",
            "copy",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            *tls_flags,
            stream_url,
        ]
        log.info("ffmpeg: %s", " ".join(cmd))
        self._ffmpeg = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
        # Drain stderr in the background so the buffer can't fill and stall ffmpeg.
        threading.Thread(
            target=self._drain_stderr, name="ffmpeg-stderr", daemon=True
        ).start()

    def _drain_stderr(self) -> None:
        proc = self._ffmpeg
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    log.debug("ffmpeg: %s", line)
        except Exception:  # pragma: no cover
            pass

    def _start_encoder(self) -> None:
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FileOutput

        cfg = self._config
        encoder = H264Encoder(
            bitrate=int(cfg.bitrate),
            repeat=True,  # inline SPS/PPS with every keyframe
            iperiod=int(cfg.keyframe_interval),
            framerate=float(cfg.fps),
            profile=self._h264_profile_name(cfg.h264_profile),
        )
        output = FileOutput(self._ffmpeg.stdin)
        self._encoder = encoder
        self._picam2.start_recording(encoder, output)
        self._attach_pre_roll_output_if_enabled()
        log.info(
            "Picamera2 H264 recording started → ffmpeg stdin (PID %d)",
            self._ffmpeg.pid if self._ffmpeg else -1,
        )
        self._apply_image_quality()

    def _apply_image_quality(self) -> None:
        """Apply persisted image-quality controls to the live camera (#182).

        Controls are JSON-encoded in ``cfg.image_quality`` and pushed to
        ``Picamera2.set_controls`` after ``start_recording`` (set_controls
        only takes effect on a running camera). Defensive: any failure
        is logged at WARNING and the streamer continues — one malformed
        control doesn't break the others or the stream itself.
        """
        try:
            controls_dict = self._config.image_quality
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("image_quality read failed: %s", exc)
            return
        if not controls_dict:
            log.debug("image_quality empty — leaving libcamera defaults in place")
            return
        applied = self._coerce_image_quality(controls_dict)
        if not applied:
            return
        try:
            self._picam2.set_controls(applied)
        except Exception as exc:
            log.warning("Picamera2.set_controls failed: %s", exc)
            return
        log.info("Image-quality controls applied: %s", applied)

    @staticmethod
    def _coerce_image_quality(raw: dict) -> dict:
        """Translate the wire dict into the shape libcamera expects.

        - Scalar floats pass through (Brightness, Contrast, Saturation,
          Sharpness, ExposureValue, Brightness)
        - Enum strings → libcamera enum values
          (NoiseReductionMode, AwbMode)
        - Unknown keys are dropped silently — the camera's reported
          ``image_controls`` catalogue gates what the dashboard offers,
          so unknown means "user agent sent something we don't support
          yet" not "user typed garbage".
        """
        scalar = (
            "Brightness",
            "Contrast",
            "Saturation",
            "Sharpness",
            "ExposureValue",
        )
        out: dict = {}
        for key, val in raw.items():
            if key in scalar:
                try:
                    out[key] = float(val)
                except (TypeError, ValueError):
                    log.warning(
                        "image_quality: %s=%r not a number — skipping", key, val
                    )
                continue
            if key in ("NoiseReductionMode", "AwbMode"):
                out[key] = _resolve_libcamera_enum(key, val)
                if out[key] is None:
                    out.pop(key)
                continue
            log.debug("image_quality: dropping unsupported key %r", key)
        return out

    @staticmethod
    def _h264_profile_name(profile: str) -> str:
        """Map our human profile names to Picamera2's H264Encoder values.

        Picamera2 accepts ``"baseline" | "main" | "high"`` — same vocabulary.
        """
        if profile in ("baseline", "main", "high"):
            return profile
        log.warning("unknown h264_profile=%r, defaulting to 'high'", profile)
        return "high"

    def _start_lores_thread(self) -> None:
        """Poll the lores stream at ~LORES_FPS and feed the callback."""
        self._lores_thread = threading.Thread(
            target=self._lores_loop, name="picam-lores", daemon=True
        )
        self._lores_thread.start()

    def _attach_pre_roll_output_if_enabled(self) -> None:
        if not self._motion_pre_roll_enabled():
            self._pre_roll_output = None
            self._pre_roll_buffer_started_monotonic = None
            return

        from picamera2.outputs import CircularOutput

        buffer_frames = max(
            1,
            round(self._motion_pre_roll_seconds() * max(1.0, float(self._config.fps))),
        )
        ring = CircularOutput(buffersize=buffer_frames)
        current_output = getattr(self._encoder, "output", None)
        if isinstance(current_output, list):
            outputs = list(current_output)
        elif current_output is None:
            outputs = []
        else:
            outputs = [current_output]
        outputs.append(ring)
        self._encoder.output = outputs
        self._pre_roll_output = ring
        self._pre_roll_buffer_started_monotonic = time.monotonic()
        log.info(
            "Attached motion pre-roll buffer (%d frames, target %.2fs)",
            buffer_frames,
            self._motion_pre_roll_seconds(),
        )

    def _motion_pre_roll_enabled(self) -> bool:
        return bool(
            self._motion_enabled
            and getattr(self._config, "motion_pre_roll_enabled", False)
        )

    def _motion_pre_roll_seconds(self) -> float:
        return float(max(0, getattr(self._config, "motion_pre_roll_seconds", 0)))

    def _available_pre_roll_seconds(self, now: float | None = None) -> float:
        if self._pre_roll_buffer_started_monotonic is None:
            return 0.0
        target = self._motion_pre_roll_seconds()
        if target <= 0:
            return 0.0
        now = time.monotonic() if now is None else now
        return min(target, max(0.0, now - self._pre_roll_buffer_started_monotonic))

    def _reset_pre_roll_recording_state(self) -> None:
        self._pre_roll_file = None
        self._pre_roll_part_path = None
        self._pre_roll_final_path = None
        self._pre_roll_actual_seconds = 0.0
        self._pre_roll_recording_started_monotonic = None
        self._pre_roll_event_started_at = None

    def _lores_loop(self) -> None:
        """Capture lores Y-plane frames and hand them to the callback.

        ``capture_array("lores")`` returns a YUV420 array shaped
        (H*3/2, W) for our 320×240 request — the top H rows are the
        Y plane, the rest is interleaved U + V. We only need the Y
        plane (grayscale luma) for frame-diff, which skips chroma
        entirely — both smaller and faster.
        """
        import numpy as np

        period = 1.0 / max(1, LORES_FPS)
        next_due = time.monotonic()
        while self._running:
            try:
                frame = self._picam2.capture_array("lores")
            except Exception as exc:
                log.warning("lores capture failed: %s — retrying", exc)
                time.sleep(0.5)
                next_due = time.monotonic()
                continue
            # Y plane only — top H rows of the YUV420 array.
            try:
                y_plane = np.ascontiguousarray(frame[:LORES_HEIGHT, :LORES_WIDTH])
                if self._frame_cb is not None:
                    self._frame_cb(y_plane)
            except Exception:
                log.exception("motion frame callback failed")
            # Sleep until the next tick — not strict, but keeps cadence.
            next_due += period
            delay = next_due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                # Falling behind — catch up but don't accumulate.
                next_due = time.monotonic()

    # --- URL / TLS helpers (mirrors StreamManager) ----------------------

    def _use_mtls(self) -> bool:
        return getattr(self._config, "has_client_cert", False)

    def _stream_url(self) -> str:
        if self._use_mtls():
            return self._config.rtsps_url
        return self._config.rtsp_url

    def _tls_flags(self) -> list[str]:
        if not self._use_mtls():
            return []
        certs_dir = self._config.certs_dir
        return [
            "-cert_file",
            os.path.join(certs_dir, "client.crt"),
            "-key_file",
            os.path.join(certs_dir, "client.key"),
            "-ca_file",
            os.path.join(certs_dir, "ca.crt"),
            "-tls_verify",
            "0",
        ]
