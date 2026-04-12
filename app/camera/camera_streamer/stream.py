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


class StreamManager:
    """Manage the ffmpeg RTSP streaming process.

    Args:
        config: ConfigManager instance.
        camera_device: Camera device path (from Platform). Defaults to /dev/video0.
    """

    def __init__(self, config, camera_device="/dev/video0"):
        self._config = config
        self._camera_device = camera_device
        self._process = None
        self._libcamera_proc = None
        self._running = False
        self._thread = None
        self._backoff = INITIAL_BACKOFF
        self._consecutive_failures = 0
        self._mtls_failed = False
        self._lock = threading.Lock()

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

    def stop(self):
        """Stop streaming and kill the ffmpeg process."""
        self._running = False
        self._kill_ffmpeg()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        log.info("Stream manager stopped")

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

            # If mTLS is enabled and we've failed twice, fall back to plain RTSP
            if (
                not self._mtls_failed
                and self._config.has_client_cert
                and self._consecutive_failures >= 2
            ):
                log.warning(
                    "mTLS connection failed %d times — falling back to plain RTSP",
                    self._consecutive_failures,
                )
                self._mtls_failed = True
                self._consecutive_failures = 0
                self._backoff = INITIAL_BACKOFF
                continue

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

    def _is_port_open(self, host, port, timeout=3):
        """Check if a TCP port is reachable."""
        import socket

        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (OSError, TimeoutError):
            return False

    @property
    def _use_mtls(self):
        """Return True if mTLS certs are available AND RTSPS is configured.

        mTLS requires both client certs and a server-side RTSPS listener.
        We detect this by checking if port 8322 is reachable. If not,
        fall back to plain RTSP to avoid connection failures.
        """
        if not self._config.has_client_cert:
            return False
        if self._mtls_failed:
            return False
        # Check if RTSPS port is actually listening
        if not self._is_port_open(self._config.server_ip, 8322):
            log.info("RTSPS port 8322 not reachable — using plain RTSP")
            self._mtls_failed = True
            return False
        return True

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

    def _build_libcamera_ffmpeg_cmd(self):
        """Build libcamera-vid → TCP → ffmpeg pipeline.

        libcamera-vid captures from the camera sensor via the ISP,
        encodes to H.264 using the GPU, and listens on a TCP port.
        ffmpeg connects to it and pushes the stream via RTSP.

        Using TCP instead of pipe avoids ffmpeg's probe timing issues
        with raw H.264 streams from stdin.
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
            "high",
            "--level",
            "4.2",
            "--bitrate",
            "4000000",  # 4 Mbps
            "--inline",  # SPS/PPS with every keyframe
            "--intra",
            "30",  # keyframe every 30 frames (~1.2s at 25fps)
            "--nopreview",
            "--listen",  # TCP server mode
            "-o",
            f"tcp://0.0.0.0:{tcp_port}",
        ]
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
        ]
        return libcamera_cmd, ffmpeg_cmd

    def _build_ffmpeg_only_cmd(self):
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

        if self._has_libcamera():
            # Use libcamera-vid pipeline (OV5647, IMX219, etc.)
            libcamera_cmd, ffmpeg_cmd = self._build_libcamera_ffmpeg_cmd()
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
                return

            with self._lock:
                # Now start ffmpeg to connect to libcamera's TCP stream
                self._process = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if hasattr(os, "setsid") else None,
                )

            log.info(
                "ffmpeg started (PID %d), streaming to %s",
                self._process.pid,
                self._config.rtsp_url,
            )
        else:
            # Direct ffmpeg v4l2 capture (camera outputs H.264 natively)
            cmd = self._build_ffmpeg_only_cmd()
            log.info("Using direct ffmpeg v4l2 capture (no libcamera)")
            log.info("ffmpeg: %s", " ".join(cmd))

            with self._lock:
                self._libcamera_proc = None
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid if hasattr(os, "setsid") else None,
                )

            log.info("ffmpeg started (PID %d)", self._process.pid)

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
