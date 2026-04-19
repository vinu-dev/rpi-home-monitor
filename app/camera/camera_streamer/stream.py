"""
RTSP stream manager.

Manages the video capture and RTSP streaming pipeline.

The OV5647 sensor (PiHut ZeroCam) outputs raw Bayer data, NOT H.264.
We use libcamera-vid to handle the full ISP pipeline:
  Bayer → demosaic → YUV → H.264 encode (via GPU)

Pipeline:
  libcamera-vid (H.264 output to stdout) | ffmpeg (RTSP push to server)

If libcamera-vid is not available, falls back to direct ffmpeg v4l2
capture (works for cameras that output H.264 natively).

Features:
- Auto-reconnect on server disconnect (exponential backoff, max 60s)
- Health monitoring (check process alive)
- Graceful shutdown on SIGTERM
- mTLS client certificate for authentication (RTSPS) when paired
"""

import logging
import os
import subprocess
import threading
import time

log = logging.getLogger("camera-streamer.stream")

# Reconnect backoff
INITIAL_BACKOFF = 2
MAX_BACKOFF = 60

# Motion detection (docs/exec-plans/motion-detection.md). ffmpeg tees a
# downsampled grayscale stream to the write end of an os.pipe() at ~5 fps;
# MotionRunner reads frames from the read end. Using a pipe fd (not a
# disk FIFO) sidesteps ffmpeg's refuse-to-overwrite logic and the various
# systemd /tmp cleanup modes. The write-end fd is inherited by the ffmpeg
# child via Popen(pass_fds=...) and referenced as ``pipe:<fd>`` in the
# ffmpeg command line.
MOTION_LORES_WIDTH = 320
MOTION_LORES_HEIGHT = 240
MOTION_LORES_FPS = 5


class StreamManager:
    """Manage the ffmpeg RTSP streaming process.

    Args:
        config: ConfigManager instance.
        camera_device: Camera device path (from Platform). Defaults to /dev/video0.
    """

    def __init__(self, config, camera_device="/dev/video0", pairing_manager=None):
        self._config = config
        self._camera_device = camera_device
        self._pairing_manager = pairing_manager
        self._process = None
        self._libcamera_proc = None
        self._running = False
        self._thread = None
        self._backoff = INITIAL_BACKOFF
        self._consecutive_failures = 0
        self._lock = threading.Lock()
        self._motion_runner = None
        # Pipe between ffmpeg (write end, inherited via pass_fds) and
        # MotionRunner (read end). Non-None only while a ffmpeg process
        # is being launched + running. See _prepare_motion_pipe.
        self._motion_read_fd: int | None = None
        self._motion_write_fd: int | None = None

    @property
    def is_streaming(self):
        """Return True if ffmpeg is currently running."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    @property
    def consecutive_failures(self):
        return self._consecutive_failures

    def start(self):
        """Start the streaming loop in a background thread."""
        if not self._config.is_configured:
            log.warning("Server not configured — streaming disabled")
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._stream_loop, daemon=True, name="stream-loop"
        )
        self._thread.start()
        log.info("Stream manager started")
        return True

    def _motion_enabled(self) -> bool:
        """True iff config wants motion detection AND wiring is in place."""
        if not getattr(self._config, "motion_detection", False):
            return False
        if self._pairing_manager is None:
            log.debug("motion_detection=true but no pairing manager — feature off")
            return False
        return True

    def _prepare_motion_pipe(self):
        """Create the ffmpeg↔MotionRunner pipe pair for one pipeline cycle.

        Returns the write-end fd (to be passed to Popen via pass_fds), or
        None if motion detection is disabled. The read-end fd is stashed
        on self for the MotionRunner to consume.
        """
        if not self._motion_enabled():
            return None
        read_fd, write_fd = os.pipe()
        # Python defaults fds to non-inheritable since 3.4; we must flip
        # the write end explicitly so the ffmpeg child sees it.
        os.set_inheritable(write_fd, True)
        self._motion_read_fd = read_fd
        self._motion_write_fd = write_fd
        log.info("Motion pipe created: read_fd=%d write_fd=%d", read_fd, write_fd)
        return write_fd

    def _close_motion_pipe_parent(self):
        """Close the write end in the parent (ffmpeg has its own copy).

        Called right after Popen succeeds. The read end stays open for
        MotionRunner. If we don't close this, the read end won't get EOF
        when ffmpeg exits.
        """
        if self._motion_write_fd is not None:
            try:
                os.close(self._motion_write_fd)
            except OSError:
                pass
            self._motion_write_fd = None

    def _cleanup_motion_pipe(self):
        """Close any lingering motion pipe fds on pipeline teardown."""
        for attr in ("_motion_write_fd", "_motion_read_fd"):
            fd = getattr(self, attr, None)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attr, None)

    def _start_motion_runner(self):
        """Spin up MotionRunner on the just-created read fd."""
        if self._motion_read_fd is None:
            return
        try:
            # Local import so numpy is only required when the feature is on.
            from camera_streamer.motion_runner import MotionRunner
        except ImportError as exc:
            log.warning(
                "MotionRunner unavailable (%s) — motion detection disabled",
                exc,
            )
            self._cleanup_motion_pipe()
            return
        try:
            self._motion_runner = MotionRunner(
                config=self._config,
                pairing_manager=self._pairing_manager,
                frame_fd=self._motion_read_fd,
            )
            # MotionRunner now owns the read fd; clear our ref so we don't
            # double-close it on pipeline teardown.
            self._motion_read_fd = None
            self._motion_runner.start()
        except Exception as exc:
            log.warning("MotionRunner failed to start: %s", exc)
            self._motion_runner = None
            self._cleanup_motion_pipe()

    def stop(self):
        """Stop streaming and kill the ffmpeg process."""
        self._running = False
        self._kill_ffmpeg()
        self._stop_motion_runner()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        log.info("Stream manager stopped")

    def _stop_motion_runner(self):
        if self._motion_runner is not None:
            try:
                self._motion_runner.stop()
            except Exception as exc:
                log.debug("MotionRunner stop failed: %s", exc)
            self._motion_runner = None
        self._cleanup_motion_pipe()

    def restart(self):
        """Restart the streaming pipeline with current config values.

        Stops the existing pipeline and starts a new one. The config
        should already be updated before calling this method.
        Returns True if restart was initiated successfully.
        """
        log.info("Restarting stream pipeline...")
        self.stop()
        return self.start()

    def _stream_loop(self):
        """Main loop: start ffmpeg, monitor, reconnect on failure."""
        while self._running:
            try:
                self._start_ffmpeg()
                self._monitor_ffmpeg()
            except Exception:
                log.exception("Unexpected error in stream loop")

            if not self._running:
                break

            # Reconnect with backoff
            self._consecutive_failures += 1
            wait = min(
                self._backoff * (2 ** (self._consecutive_failures - 1)), MAX_BACKOFF
            )
            log.info(
                "Stream ended (failure #%d), reconnecting in %ds...",
                self._consecutive_failures,
                wait,
            )
            # Sleep in small increments so we can stop quickly
            for _ in range(int(wait * 10)):
                if not self._running:
                    return
                time.sleep(0.1)

    @property
    def _use_mtls(self):
        """Return True if mTLS certs are available.

        When the camera is paired (client cert exists), always use mTLS.
        Connection failures are handled by the reconnect backoff loop,
        not by falling back to plain RTSP — the server may simply be
        slower to boot than the camera.
        """
        return self._config.has_client_cert

    @property
    def _stream_url(self):
        """Return RTSPS URL if mTLS is available, otherwise plain RTSP."""
        if self._use_mtls:
            return self._config.rtsps_url
        return self._config.rtsp_url

    def _tls_flags(self):
        """Return ffmpeg TLS flags for mTLS client cert authentication.

        ffmpeg's RTSP muxer passes TLS options through to the underlying
        TLS protocol handler. The option names use the tls_ prefix and
        are passed as output options before the URL.
        """
        if not self._use_mtls:
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

    def _has_libcamera(self):
        """Check if libcamera-vid is available."""
        import shutil

        return shutil.which("libcamera-vid") is not None

    def _build_libcamera_ffmpeg_cmd(self, motion_write_fd: int | None = None):
        """Build libcamera-vid → TCP → ffmpeg pipeline.

        libcamera-vid captures from the camera sensor via the ISP,
        encodes to H.264 using the GPU, and listens on a TCP port.
        ffmpeg connects to it and pushes the stream via RTSP.

        When ``motion_write_fd`` is provided, ffmpeg gets an additional
        output writing grayscale 320x240@5fps raw frames to that fd —
        consumed by MotionRunner at the other end of the pipe.

        Using TCP instead of pipe for the libcamera→ffmpeg hop avoids
        ffmpeg's probe timing issues with raw H.264 streams from stdin.
        """
        cfg = self._config
        tcp_port = 8888

        # libcamera-vid: capture H.264, serve on TCP
        libcamera_cmd = [
            "libcamera-vid",
            "-t",
            "0",  # run forever
            "--width",
            str(cfg.width),
            "--height",
            str(cfg.height),
            "--framerate",
            str(cfg.fps),
            "--codec",
            "h264",
            "--profile",
            cfg.h264_profile,
            "--level",
            "4.2",
            "--bitrate",
            str(cfg.bitrate),
            "--inline",  # SPS/PPS with every keyframe
            "--intra",
            str(cfg.keyframe_interval),
            "--nopreview",
            "--listen",  # TCP server mode
            "-o",
            f"tcp://0.0.0.0:{tcp_port}",
        ]
        # Rotation and flip (OV5647 supports 0 and 180 via --rotation,
        # plus independent --hflip and --vflip)
        if cfg.rotation == 180:
            libcamera_cmd.extend(["--rotation", "180"])
        if cfg.hflip:
            libcamera_cmd.append("--hflip")
        if cfg.vflip:
            libcamera_cmd.append("--vflip")
        # ffmpeg: read H.264 from TCP, push to RTSP
        # Key: probesize must be large enough for ffmpeg to see a keyframe
        # with SPS/PPS from libcamera-vid. At 4Mbps + 25fps, a keyframe
        # arrives every ~2s, so we need 10-15MB of probe data.
        ffmpeg_cmd = [
            "ffmpeg",
            "-nostdin",
            "-use_wallclock_as_timestamps",
            "1",
            "-fflags",
            "+genpts",
            "-probesize",
            "50000000",  # 50MB — ample room for keyframes
            "-analyzeduration",
            "30000000",  # 30s — generous probe window for SPS/PPS
            "-f",
            "h264",  # tell ffmpeg it's raw H.264
            "-i",
            f"tcp://127.0.0.1:{tcp_port}",
            "-c:v",
            "copy",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            *self._tls_flags(),
            self._stream_url,
            *self._motion_tee_args(motion_write_fd),
        ]
        return libcamera_cmd, ffmpeg_cmd

    def _motion_tee_args(self, write_fd: int | None):
        """Return extra ffmpeg output args that fan a lores YUV stream to
        the motion pipe. Empty list when motion detection is off or no
        pipe fd has been prepared.

        The output chain decodes H.264 (unavoidable for scale+format),
        downsamples to 320x240 grayscale at 5 fps, and writes raw Y bytes
        to ``pipe:<write_fd>`` — an os.pipe() whose write end is inherited
        by the ffmpeg child via Popen(pass_fds=...). No on-disk FIFO is
        involved, so ffmpeg's refuse-to-overwrite logic never fires.
        """
        if write_fd is None:
            return []
        return [
            "-map",
            "0:v",
            "-vf",
            f"scale={MOTION_LORES_WIDTH}:{MOTION_LORES_HEIGHT},format=gray",
            "-r",
            str(MOTION_LORES_FPS),
            "-f",
            "rawvideo",
            f"pipe:{write_fd}",
        ]

    def _build_ffmpeg_only_cmd(self, motion_write_fd: int | None = None):
        """Build direct ffmpeg v4l2 command (for cameras with native H.264)."""
        cfg = self._config
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-f",
            "v4l2",
            "-input_format",
            "h264",
            "-video_size",
            f"{cfg.width}x{cfg.height}",
            "-framerate",
            str(cfg.fps),
            "-i",
            self._camera_device,
            "-c:v",
            "copy",
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            *self._tls_flags(),
            self._stream_url,
            *self._motion_tee_args(motion_write_fd),
        ]
        return cmd

    def _start_ffmpeg(self):
        """Launch the streaming pipeline."""
        import shutil

        log.info(
            "Stream config: device=%s resolution=%dx%d fps=%d "
            "server=%s:%s camera_id=%s",
            self._camera_device,
            self._config.width,
            self._config.height,
            self._config.fps,
            self._config.server_ip,
            self._config.server_port,
            self._config.camera_id,
        )
        log.info("Stream target URL: %s (mTLS=%s)", self._stream_url, self._use_mtls)

        # Check if video device exists before starting
        if not os.path.exists(self._camera_device):
            log.error("%s not found — camera not detected", self._camera_device)
            return

        if not shutil.which("ffmpeg"):
            log.error("ffmpeg binary not found in PATH — cannot stream")
            return

        # Create the motion pipe *before* ffmpeg so we have a write fd to
        # pass through via pass_fds. The read fd is stashed on self and
        # handed to MotionRunner after ffmpeg is up.
        motion_write_fd = self._prepare_motion_pipe()
        pass_fds = (motion_write_fd,) if motion_write_fd is not None else ()

        if self._has_libcamera():
            # Use libcamera-vid pipeline (OV5647, IMX219, etc.)
            libcamera_cmd, ffmpeg_cmd = self._build_libcamera_ffmpeg_cmd(
                motion_write_fd
            )
            log.info("Using libcamera pipeline (sensor outputs raw Bayer)")
            log.info("libcamera-vid: %s", " ".join(libcamera_cmd))
            log.info("ffmpeg: %s", " ".join(ffmpeg_cmd))

            with self._lock:
                # Start libcamera-vid first (TCP server mode)
                self._libcamera_proc = subprocess.Popen(
                    libcamera_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if hasattr(os, "setsid") else None,
                )
            log.info(
                "libcamera-vid started (PID %d), waiting for TCP...",
                self._libcamera_proc.pid,
            )

            # Wait for libcamera-vid to initialize camera + start TCP server
            # OV5647 on Zero 2W needs ~3-5s to start producing frames
            time.sleep(5)

            if self._libcamera_proc.poll() is not None:
                log.error(
                    "libcamera-vid exited early (code %d)",
                    self._libcamera_proc.returncode,
                )
                self._cleanup_motion_pipe()
                return

            with self._lock:
                # Now start ffmpeg to connect to libcamera's TCP stream
                self._process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if hasattr(os, "setsid") else None,
                    pass_fds=pass_fds,
                )

            log.info(
                "ffmpeg started (PID %d), streaming to %s",
                self._process.pid,
                self._config.rtsp_url,
            )
        else:
            # Direct ffmpeg v4l2 capture (camera outputs H.264 natively)
            cmd = self._build_ffmpeg_only_cmd(motion_write_fd)
            log.info("Using direct ffmpeg v4l2 capture (no libcamera)")
            log.info("ffmpeg: %s", " ".join(cmd))

            with self._lock:
                self._libcamera_proc = None
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if hasattr(os, "setsid") else None,
                    pass_fds=pass_fds,
                )

            log.info("ffmpeg started (PID %d)", self._process.pid)

        # ffmpeg is up. Close our copy of the write end and spin up the
        # MotionRunner on the read end. Order matters: close-in-parent
        # first so the read end sees EOF when ffmpeg exits.
        self._close_motion_pipe_parent()
        self._start_motion_runner()

    def _monitor_ffmpeg(self):
        """Wait for the ffmpeg process to finish. Resets backoff on success."""
        proc = self._process
        if proc is None:
            return

        # Read stderr in background to avoid deadlock
        stderr_lines = []

        def _read_stderr():
            for line in proc.stderr:
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if decoded:
                    stderr_lines.append(decoded)
                    log.debug("ffmpeg: %s", decoded)

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        # Wait for process to exit
        proc.wait()
        stderr_thread.join(timeout=2)

        returncode = proc.returncode
        with self._lock:
            self._process = None

        if returncode == 0:
            # Clean exit (shouldn't happen during normal streaming)
            log.info("ffmpeg exited cleanly")
            self._consecutive_failures = 0
            self._backoff = INITIAL_BACKOFF
        else:
            last_err = stderr_lines[-5:] if stderr_lines else ["(no output)"]
            log.warning(
                "ffmpeg exited with code %d. Last output:\n  %s",
                returncode,
                "\n  ".join(last_err),
            )

    def _kill_ffmpeg(self):
        """Kill the streaming pipeline (ffmpeg and libcamera-vid if running)."""
        with self._lock:
            proc = self._process
            libcam = getattr(self, "_libcamera_proc", None)

        for name, p in [("ffmpeg", proc), ("libcamera-vid", libcam)]:
            if p is None:
                continue
            try:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=2)
                log.info("%s process terminated", name)
            except OSError:
                pass

        with self._lock:
            self._process = None
            self._libcamera_proc = None

        # Tear down the motion runner + its pipe. Needed on every
        # ffmpeg exit — the reconnect loop will _start_ffmpeg() again
        # and allocate a fresh pipe.
        self._stop_motion_runner()
