"""
Streaming service — manages video pipelines per camera.

For each confirmed camera, runs ffmpeg processes that:
1. Convert RTSP stream to HLS segments for live view
2. Record 3-minute MP4 clips for playback
3. Extract periodic snapshots (every 30s)

Architecture:
  Camera → RTSP push → mediamtx (:8554) → ffmpeg pipelines

mediamtx receives RTSP pushes from cameras and republishes them
at rtsp://localhost:8554/<stream-name>. This service pulls from
mediamtx to create the HLS/recording/snapshot outputs.
"""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("monitor.streaming")

MEDIAMTX_URL = "rtsp://127.0.0.1:8554"
RTSP_TIMEOUT_US = "5000000"  # 5s RTSP socket timeout (microseconds)
SNAPSHOT_INTERVAL = 30  # seconds between snapshot updates
HLS_SEGMENT_DURATION = 2  # seconds per HLS segment
HLS_LIST_SIZE = 5  # rolling window of HLS segments
CLIP_DURATION = 180  # 3-minute clips
FFMPEG_LOG_DIR = Path("/data/logs/ffmpeg")


class StreamingService:
    """Manages per-camera ffmpeg video pipelines."""

    def __init__(self, live_dir, recordings_dir, clip_duration=CLIP_DURATION):
        self._live_dir = Path(live_dir)
        self._recordings_dir = Path(recordings_dir)
        self._clip_duration = clip_duration
        self._hls_procs = {}  # cam_id -> Popen
        self._rec_procs = {}  # cam_id -> Popen
        self._snap_threads = {}  # cam_id -> Thread
        self._running = False
        self._lock = threading.Lock()

    @property
    def active_cameras(self):
        """Return list of camera IDs with active pipelines."""
        with self._lock:
            return list(self._hls_procs.keys())

    @property
    def recordings_dir(self):
        """Current recordings directory."""
        return str(self._recordings_dir)

    def update_recordings_dir(self, new_dir):
        """Change recordings directory and restart all recorder pipelines.

        Called by StorageManager when switching between internal/USB storage.
        HLS and snapshot pipelines are unaffected (they use live_dir).
        """
        old_dir = str(self._recordings_dir)
        self._recordings_dir = Path(new_dir)
        log.info("Recordings dir changed: %s -> %s", old_dir, new_dir)

        # Restart recorders for all active cameras
        active = self.active_cameras
        for cam_id in active:
            self._stop_process(cam_id, self._rec_procs, "recorder")
            rtsp_url = f"{MEDIAMTX_URL}/{cam_id}"
            self._start_recorder(cam_id, rtsp_url)
            log.info("Restarted recorder for %s with new dir", cam_id)

    def start(self):
        """Start the streaming service and watchdog thread."""
        self._running = True
        self._start_watchdog()
        log.info("Streaming service started")

    def stop(self):
        """Stop all pipelines and clean up."""
        self._running = False
        cam_ids = list(self._hls_procs.keys())
        for cam_id in cam_ids:
            self.stop_camera(cam_id)
        log.info("Streaming service stopped")

    def start_camera(self, cam_id, stream_name=None):
        """Start all pipelines for a camera.

        Args:
            cam_id: Camera identifier
            stream_name: RTSP stream name on mediamtx (defaults to cam_id)
        """
        if not self._running:
            log.warning("Streaming service not running")
            return False

        stream_name = stream_name or cam_id
        rtsp_url = f"{MEDIAMTX_URL}/{stream_name}"

        # Create output directories
        cam_live = self._live_dir / cam_id
        cam_live.mkdir(parents=True, exist_ok=True)

        log.info("Starting pipelines for camera %s (source: %s)", cam_id, rtsp_url)

        # Start HLS pipeline
        self._start_hls(cam_id, rtsp_url)

        # Start recording pipeline
        self._start_recorder(cam_id, rtsp_url)

        # Start snapshot thread
        self._start_snapshots(cam_id, rtsp_url)

        return True

    def stop_camera(self, cam_id):
        """Stop all pipelines for a camera."""
        log.info("Stopping pipelines for camera %s", cam_id)
        self._stop_process(cam_id, self._hls_procs, "HLS")
        self._stop_process(cam_id, self._rec_procs, "recorder")

        # Stop snapshot thread
        with self._lock:
            if cam_id in self._snap_threads:
                del self._snap_threads[cam_id]

        # Clean up stale HLS segments
        cam_live = self._live_dir / cam_id
        if cam_live.is_dir():
            for ts in cam_live.glob("segment_*.ts"):
                try:
                    ts.unlink()
                except OSError:
                    pass

    def is_camera_active(self, cam_id):
        """Check if a camera has active pipelines."""
        with self._lock:
            proc = self._hls_procs.get(cam_id)
            return proc is not None and proc.poll() is None

    def restart_camera(self, cam_id, stream_name=None):
        """Restart pipelines for a camera."""
        self.stop_camera(cam_id)
        time.sleep(1)  # Brief pause between stop/start
        return self.start_camera(cam_id, stream_name)

    # --- HLS pipeline ---

    def _start_hls(self, cam_id, rtsp_url):
        """Start ffmpeg RTSP → HLS conversion."""
        output_dir = self._live_dir / cam_id
        playlist = output_dir / "stream.m3u8"
        segment_pattern = str(output_dir / "segment_%03d.ts")

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            RTSP_TIMEOUT_US,
            "-i",
            rtsp_url,
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-f",
            "hls",
            "-hls_time",
            str(HLS_SEGMENT_DURATION),
            "-hls_list_size",
            str(HLS_LIST_SIZE),
            "-hls_flags",
            "delete_segments+append_list",
            "-hls_segment_filename",
            segment_pattern,
            str(playlist),
        ]

        proc = self._launch_ffmpeg(cmd, f"hls-{cam_id}")
        if proc:
            with self._lock:
                self._hls_procs[cam_id] = proc
            log.info("HLS pipeline started for %s (PID %d)", cam_id, proc.pid)

    # --- Recording pipeline ---

    def _start_recorder(self, cam_id, rtsp_url):
        """Start ffmpeg RTSP → segmented MP4 recording."""
        # Use strftime pattern for auto-dated directories
        # ffmpeg segment_format creates: /data/recordings/<cam>/YYYY-MM-DD/HH-MM-SS.mp4
        cam_rec_dir = self._recordings_dir / cam_id
        cam_rec_dir.mkdir(parents=True, exist_ok=True)

        # ffmpeg segment muxer can't create subdirectories, so we
        # pre-create today's date dir and start a thread to create
        # tomorrow's dir at midnight.
        today = datetime.now().strftime("%Y-%m-%d")
        today_dir = cam_rec_dir / today
        today_dir.mkdir(parents=True, exist_ok=True)

        # Start a thread that creates the next day's dir before midnight
        self._start_dir_creator(cam_id, cam_rec_dir)

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            RTSP_TIMEOUT_US,
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            rtsp_url,
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self._clip_duration),
            "-segment_format",
            "mp4",
            "-segment_atclocktime",
            "1",
            "-segment_clocktime_wrap_duration",
            "30",
            "-strftime",
            "1",
            "-reset_timestamps",
            "1",
            str(cam_rec_dir / "%Y-%m-%d" / "%H-%M-%S.mp4"),
        ]

        proc = self._launch_ffmpeg(cmd, f"rec-{cam_id}")
        if proc:
            with self._lock:
                self._rec_procs[cam_id] = proc
            log.info("Recorder started for %s (PID %d)", cam_id, proc.pid)

    def _start_dir_creator(self, cam_id, rec_dir):
        """Pre-create date directories so ffmpeg segment muxer doesn't fail at midnight."""

        def _dir_loop():
            while self._running and cam_id in self._rec_procs:
                # Create today's and tomorrow's dirs
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                tomorrow = (
                    now.replace(hour=0, minute=0, second=0)
                    + __import__("datetime").timedelta(days=1)
                ).strftime("%Y-%m-%d")
                for d in [today, tomorrow]:
                    (rec_dir / d).mkdir(parents=True, exist_ok=True)
                # Check every 10 minutes
                for _ in range(600):
                    if not self._running or cam_id not in self._rec_procs:
                        return
                    time.sleep(1)

        t = threading.Thread(target=_dir_loop, daemon=True, name=f"dirs-{cam_id}")
        t.start()

    # --- Process watchdog ---

    WATCHDOG_INTERVAL = 30  # seconds between health checks
    STALE_CLIP_FACTOR = 2  # restart if newest clip > clip_duration * factor

    def _start_watchdog(self):
        """Start a watchdog thread that restarts dead ffmpeg processes."""

        def _watchdog_loop():
            while self._running:
                self._check_processes()
                for _ in range(self.WATCHDOG_INTERVAL * 10):
                    if not self._running:
                        return
                    time.sleep(0.1)

        t = threading.Thread(target=_watchdog_loop, daemon=True, name="stream-watchdog")
        t.start()

    def _check_processes(self):
        """Check all ffmpeg processes, restart any that have died or stalled."""
        with self._lock:
            cam_ids = list(self._hls_procs.keys())

        for cam_id in cam_ids:
            rtsp_url = f"{MEDIAMTX_URL}/{cam_id}"

            # Check HLS process
            with self._lock:
                hls_proc = self._hls_procs.get(cam_id)
            if hls_proc and hls_proc.poll() is not None:
                log.warning(
                    "HLS process died for %s (PID=%d, exit=%s), restarting",
                    cam_id,
                    hls_proc.pid,
                    hls_proc.returncode,
                )
                self._close_proc_log(hls_proc)
                with self._lock:
                    self._hls_procs.pop(cam_id, None)
                self._start_hls(cam_id, rtsp_url)

            # Check recorder process — dead or stalled
            with self._lock:
                rec_proc = self._rec_procs.get(cam_id)
            if rec_proc and rec_proc.poll() is not None:
                log.warning(
                    "Recorder died for %s (PID=%d, exit=%s), restarting",
                    cam_id,
                    rec_proc.pid,
                    rec_proc.returncode,
                )
                self._close_proc_log(rec_proc)
                with self._lock:
                    self._rec_procs.pop(cam_id, None)
                self._start_recorder(cam_id, rtsp_url)
            elif rec_proc and rec_proc.poll() is None:
                # Process alive — check if it's actually writing clips
                if self._is_recorder_stale(cam_id):
                    log.warning(
                        "Recorder stalled for %s (no new clips), force-killing PID %d",
                        cam_id,
                        rec_proc.pid,
                    )
                    self._stop_process(cam_id, self._rec_procs, "stale-rec")
                    self._start_recorder(cam_id, rtsp_url)

    def _is_recorder_stale(self, cam_id):
        """Check if recorder is alive but not producing clips.

        Returns True if the newest clip for this camera is older than
        clip_duration * STALE_CLIP_FACTOR seconds. This catches ffmpeg
        segment muxer hangs where the process stays alive but stops
        writing (common on exFAT/NTFS filesystems).
        """
        cam_dir = self._recordings_dir / cam_id
        if not cam_dir.is_dir():
            return False

        # Find the newest .mp4 file across all date subdirectories
        newest_mtime = 0
        try:
            for mp4 in cam_dir.rglob("*.mp4"):
                try:
                    mt = mp4.stat().st_mtime
                    if mt > newest_mtime:
                        newest_mtime = mt
                except OSError:
                    continue
        except OSError:
            return False

        if newest_mtime == 0:
            # No clips yet — recorder may still be writing the first one.
            # Give it one full clip duration before declaring stale.
            return False

        age = time.time() - newest_mtime
        threshold = self._clip_duration * self.STALE_CLIP_FACTOR
        if age > threshold:
            log.debug(
                "Newest clip for %s is %.0fs old (threshold=%ds)",
                cam_id,
                age,
                threshold,
            )
            return True
        return False

    # --- Snapshot extraction ---

    def _start_snapshots(self, cam_id, rtsp_url):
        """Start periodic snapshot extraction in a thread."""

        def _snap_loop():
            while self._running and cam_id in self._snap_threads:
                self._take_snapshot(cam_id, rtsp_url)
                # Sleep in increments for responsive shutdown
                for _ in range(SNAPSHOT_INTERVAL * 10):
                    if not self._running or cam_id not in self._snap_threads:
                        return
                    time.sleep(0.1)

        t = threading.Thread(target=_snap_loop, daemon=True, name=f"snap-{cam_id}")
        with self._lock:
            self._snap_threads[cam_id] = t
        t.start()

    def _take_snapshot(self, cam_id, rtsp_url):
        """Extract a single JPEG frame from the RTSP stream."""
        output = self._live_dir / cam_id / "snapshot.jpg"
        tmp = output.with_suffix(".tmp.jpg")

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            RTSP_TIMEOUT_US,
            "-i",
            rtsp_url,
            "-frames:v",
            "1",
            "-q:v",
            "5",
            str(tmp),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0 and tmp.is_file():
                # Atomic rename so readers never see a partial file
                tmp.rename(output)
        except (subprocess.TimeoutExpired, OSError) as e:
            log.debug("Snapshot failed for %s: %s", cam_id, e)
        finally:
            # Clean up temp file
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    # --- Utility ---

    def _launch_ffmpeg(self, cmd, label):
        """Launch an ffmpeg subprocess with stderr logged to file.

        Each pipeline gets its own log file under /data/logs/ffmpeg/
        for post-mortem debugging of crashes and stalls.
        """
        try:
            # Create log directory and open stderr log file
            stderr_dest = subprocess.PIPE
            log_file = None
            try:
                FFMPEG_LOG_DIR.mkdir(parents=True, exist_ok=True)
                log_path = FFMPEG_LOG_DIR / f"{label}.log"
                log_file = open(log_path, "a")
                stderr_dest = log_file
                log.debug("ffmpeg stderr → %s", log_path)
            except OSError:
                pass  # Fall back to PIPE if log dir isn't writable

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=stderr_dest,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
            # Attach log file handle so we can close it on stop
            proc._log_file = log_file  # type: ignore[attr-defined]
            return proc
        except FileNotFoundError:
            log.error("ffmpeg not found — cannot start %s", label)
        except OSError as e:
            log.error("Failed to start ffmpeg for %s: %s", label, e)
        return None

    @staticmethod
    def _close_proc_log(proc):
        """Close the stderr log file attached to an ffmpeg process."""
        log_file = getattr(proc, "_log_file", None)
        if log_file:
            try:
                log_file.close()
            except OSError:
                pass

    def _stop_process(self, cam_id, proc_dict, label):
        """Stop an ffmpeg process gracefully and close its log file."""
        with self._lock:
            proc = proc_dict.pop(cam_id, None)
        if proc is None:
            return

        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            log.info(
                "%s process stopped for %s (PID %d, exit=%s)",
                label,
                cam_id,
                proc.pid,
                proc.returncode,
            )
        except OSError:
            pass
        finally:
            # Close the stderr log file handle
            log_file = getattr(proc, "_log_file", None)
            if log_file:
                try:
                    log_file.close()
                except OSError:
                    pass


def create_recording_dirs(recordings_dir, cam_id):
    """Ensure recording directory exists with today's date subdirectory.

    Called by the recorder's segment pattern, but ffmpeg can't create
    nested dirs. This pre-creates today's directory.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    path = Path(recordings_dir) / cam_id / today
    path.mkdir(parents=True, exist_ok=True)
    return path
