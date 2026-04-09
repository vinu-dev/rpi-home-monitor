"""
RTSP stream manager.

Manages the ffmpeg process that captures video from v4l2
and streams it to the home server over RTSP.

Features:
- Auto-reconnect on server disconnect (exponential backoff, max 60s)
- Health monitoring (check ffmpeg process alive)
- Graceful shutdown on SIGTERM
- Phase 2: mTLS client certificate for authentication (RTSPS)

ffmpeg command:
  ffmpeg -f v4l2 -input_format h264 -video_size 1920x1080 -framerate 25
         -i /dev/video0 -c:v copy -f rtsp -rtsp_transport tcp
         rtsp://<server>:8554/<stream-name>
"""
import os
import signal
import subprocess
import threading
import time
import logging

log = logging.getLogger("camera-streamer.stream")

# Reconnect backoff
INITIAL_BACKOFF = 2
MAX_BACKOFF = 60


class StreamManager:
    """Manage the ffmpeg RTSP streaming process."""

    def __init__(self, config):
        self._config = config
        self._process = None
        self._running = False
        self._thread = None
        self._backoff = INITIAL_BACKOFF
        self._consecutive_failures = 0
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

            # Reconnect with backoff
            self._consecutive_failures += 1
            wait = min(self._backoff * (2 ** (self._consecutive_failures - 1)), MAX_BACKOFF)
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

    def _build_ffmpeg_cmd(self):
        """Build the ffmpeg command line."""
        cfg = self._config
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-f", "v4l2",
            "-input_format", "h264",
            "-video_size", f"{cfg.width}x{cfg.height}",
            "-framerate", str(cfg.fps),
            "-i", "/dev/video0",
            "-c:v", "copy",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            cfg.rtsp_url,
        ]
        return cmd

    def _start_ffmpeg(self):
        """Launch the ffmpeg process."""
        cmd = self._build_ffmpeg_cmd()
        log.info("Starting ffmpeg: %s", " ".join(cmd))

        with self._lock:
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
        """Kill the ffmpeg process if running."""
        with self._lock:
            proc = self._process
        if proc is None:
            return

        try:
            # Try graceful termination first
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill
                proc.kill()
                proc.wait(timeout=2)
            log.info("ffmpeg process terminated")
        except OSError:
            pass
        finally:
            with self._lock:
                self._process = None
