"""
Streaming service — per-camera snapshot extraction + recorder ownership (ADR-0017).

Architecture (post-ADR-0017):
  Camera → RTSP push → mediamtx (:8554) → consumers
    - Live view: MediaMTX WebRTC WHEP direct to browser (no server-side HLS).
    - Recording: owned by RecordingScheduler (started per mode/schedule).
    - Snapshot: single long-lived ffmpeg per camera pulling every 30 s.

The old "always-on" HLS muxer and 30-second snapshot-respawn thread are gone.
A deliberately-stopped process is NOT restarted by the watchdog — intent is
tracked via `_snap_intent` / `_recorder_intent` dicts so we can distinguish
"died unexpectedly" (restart) from "asked to stop" (leave alone).
"""

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("monitor.streaming")

MEDIAMTX_URL = "rtsp://127.0.0.1:8554"
RTSP_TIMEOUT_US = "5000000"  # 5 s RTSP socket timeout (microseconds)
SNAPSHOT_INTERVAL = 30  # seconds between snapshot updates
CLIP_DURATION = 180  # default segment duration if caller doesn't override
FFMPEG_LOG_DIR = Path("/data/logs/ffmpeg")


def finalize_completed_segments(
    cam_rec_dir: Path, segments_log: Path, last_offset: int
) -> int:
    """Rename newly-completed `.mp4.part` files to `.mp4`.

    ffmpeg's segment muxer appends one line per completed segment to
    ``segments_log`` — the line arrives *after* the file is closed, so
    "in the log" is a strict "safe to rename" signal. The log may use
    either the original `.part` filename or the base name depending on
    ffmpeg version; we handle both.

    Args:
        cam_rec_dir: Directory containing the .part files.
        segments_log: ffmpeg's -segment_list manifest.
        last_offset: Byte offset into the log we last consumed (so we
            don't re-process lines on each poll).

    Returns:
        New byte offset to pass on the next call.
    """
    if not segments_log.exists():
        return last_offset
    try:
        size = segments_log.stat().st_size
    except OSError:
        return last_offset
    if size <= last_offset:
        return last_offset

    try:
        with open(segments_log, "rb") as f:
            f.seek(last_offset)
            raw = f.read()
    except OSError:
        return last_offset

    new_offset = last_offset + len(raw)
    for line in raw.splitlines():
        name = line.decode("utf-8", errors="replace").strip()
        if not name:
            continue
        # Log may contain just the basename, a `.mp4.part`, a bare `.mp4`,
        # or a full path. Normalise to `<stem>.mp4.part` inside cam_rec_dir.
        base = Path(name).name
        if base.endswith(".mp4.part"):
            part = cam_rec_dir / base
            final = cam_rec_dir / base[: -len(".part")]
        elif base.endswith(".mp4"):
            part = cam_rec_dir / f"{base}.part"
            final = cam_rec_dir / base
        else:
            continue

        if not part.exists():
            continue
        try:
            # `.mp4` may already exist if a previous pass crashed between
            # rename + segment_list flush; os.replace is atomic on POSIX
            # and overwrites on Windows.
            import os as _os

            _os.replace(part, final)
        except OSError as exc:
            log.warning("Failed to finalise %s → %s: %s", part.name, final.name, exc)
    return new_offset


# Legacy constants kept for backwards-compat with import sites that still
# reference them. HLS is no longer used server-side for live view.
HLS_SEGMENT_DURATION = 2
HLS_LIST_SIZE = 5


class StreamingService:
    """Per-camera snapshot + recorder process manager (ADR-0017).

    Args:
        live_dir: directory where snapshot.jpg files live (one subdir per cam).
        recordings_dir: base directory for recorded segments.
        clip_duration: recorder segment length (seconds).
    """

    def __init__(self, live_dir, recordings_dir, clip_duration=CLIP_DURATION):
        self._live_dir = Path(live_dir)
        self._recordings_dir = Path(recordings_dir)
        self._clip_duration = clip_duration
        self._snap_procs: dict = {}  # cam_id -> Popen (long-lived snapshot ffmpeg)
        self._rec_procs: dict = {}  # cam_id -> Popen (recorder — owned here)
        self._snap_intent: dict = {}  # cam_id -> "wanted" | "stopped"
        self._recorder_intent: dict = {}  # cam_id -> "wanted" | "stopped"
        self._running = False
        self._lock = threading.Lock()

    # --- Introspection ----------------------------------------------------

    @property
    def active_cameras(self):
        """Return list of camera IDs with an active snapshot pipeline."""
        with self._lock:
            return list(self._snap_procs.keys())

    @property
    def recordings_dir(self):
        """Current recordings directory (string)."""
        return str(self._recordings_dir)

    # --- Configuration updates --------------------------------------------

    def update_recordings_dir(self, new_dir):
        """Change recordings directory; restart any in-flight recorder."""
        old_dir = str(self._recordings_dir)
        self._recordings_dir = Path(new_dir)
        log.info("Recordings dir changed: %s -> %s", old_dir, new_dir)

        with self._lock:
            cam_ids = list(self._rec_procs.keys())
        for cam_id in cam_ids:
            rtsp_url = f"{MEDIAMTX_URL}/{cam_id}"
            self.stop_recorder(cam_id)
            self.start_recorder(cam_id, rtsp_url)

    def set_clip_duration(self, new_duration):
        """Update recorder segment duration; restart active recorders."""
        if new_duration == self._clip_duration:
            return
        self._clip_duration = new_duration
        with self._lock:
            cam_ids = list(self._rec_procs.keys())
        for cam_id in cam_ids:
            rtsp_url = f"{MEDIAMTX_URL}/{cam_id}"
            self.stop_recorder(cam_id)
            self.start_recorder(cam_id, rtsp_url)

    # --- Lifecycle --------------------------------------------------------

    def start(self):
        """Start the service + watchdog thread."""
        self._running = True
        self._start_watchdog()
        log.info("Streaming service started (on-demand mode, ADR-0017)")

    def stop(self):
        """Stop all pipelines and clean up."""
        self._running = False
        with self._lock:
            snap_ids = list(self._snap_procs.keys())
            rec_ids = list(self._rec_procs.keys())
        for cam_id in snap_ids:
            self.stop_snapshot(cam_id)
        for cam_id in rec_ids:
            self.stop_recorder(cam_id)
        log.info("Streaming service stopped")

    # --- Per-camera convenience wrappers ----------------------------------

    def start_camera(self, cam_id, stream_name=None):
        """Start the long-lived snapshot ffmpeg for a camera.

        Recording is NOT started here — that is the scheduler's job.
        """
        if not self._running:
            log.warning("Streaming service not running")
            return False

        stream_name = stream_name or cam_id
        rtsp_url = f"{MEDIAMTX_URL}/{stream_name}"

        (self._live_dir / cam_id).mkdir(parents=True, exist_ok=True)

        log.info("Starting snapshot pipeline for %s", cam_id)
        self.start_snapshot(cam_id, rtsp_url)
        return True

    def stop_camera(self, cam_id):
        """Deliberately stop all pipelines for a camera."""
        log.info("Stopping pipelines for camera %s", cam_id)
        self.stop_snapshot(cam_id)
        self.stop_recorder(cam_id)

    def is_camera_active(self, cam_id):
        """True iff snapshot pipeline is alive."""
        with self._lock:
            proc = self._snap_procs.get(cam_id)
            return proc is not None and proc.poll() is None

    def restart_camera(self, cam_id, stream_name=None):
        """Restart snapshot pipeline (kept for legacy callers)."""
        self.stop_camera(cam_id)
        time.sleep(0.5)
        return self.start_camera(cam_id, stream_name)

    # --- Snapshot pipeline (single long-lived ffmpeg, -update 1) ----------

    def start_snapshot(self, cam_id, rtsp_url):
        """Start a long-lived ffmpeg that writes snapshot.jpg every 30 s."""
        with self._lock:
            existing = self._snap_procs.get(cam_id)
            if existing and existing.poll() is None:
                return  # already running
            self._snap_intent[cam_id] = "wanted"

        out_dir = self._live_dir / cam_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "snapshot.jpg"

        # ffmpeg's -update 1 + image2 muxer atomically rewrites snapshot.jpg.
        # Built-in reconnect flags handle transient RTSP outages without a
        # Python-side respawn thread.
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            RTSP_TIMEOUT_US,
            "-reconnect",
            "1",
            "-reconnect_at_eof",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "10",
            "-i",
            rtsp_url,
            "-vf",
            f"fps=1/{SNAPSHOT_INTERVAL}",
            "-update",
            "1",
            "-q:v",
            "5",
            "-f",
            "image2",
            "-y",
            str(out),
        ]
        proc = self._launch_ffmpeg(cmd, f"snap-{cam_id}")
        if proc:
            with self._lock:
                self._snap_procs[cam_id] = proc
            log.info("Snapshot pipeline started for %s (PID %d)", cam_id, proc.pid)

    def stop_snapshot(self, cam_id):
        """Deliberately stop the snapshot pipeline for a camera."""
        with self._lock:
            self._snap_intent[cam_id] = "stopped"
        self._stop_process(cam_id, self._snap_procs, "snap")

    # --- Recorder pipeline (owned by scheduler, started on demand) --------

    def start_recorder(self, cam_id, rtsp_url):
        """Start a segmented MP4 recorder for a camera (called by scheduler).

        Idempotent: if a recorder is already alive, this is a no-op.
        Segment directory structure: <recordings_dir>/<cam_id>/YYYYMMDD_HHMMSS.mp4

        Partial-write discipline (docs/exec-plans/motion-detection.md):
        ffmpeg writes each segment as ``<stem>.mp4.part`` and appends the
        finished filename to ``.segments.log`` on clean close. A finaliser
        thread (started per cam on first use) tails that log and renames
        ``.mp4.part → .mp4`` — so downstream code (recordings list,
        motion clip correlator) only ever sees fully-written clips.
        """
        with self._lock:
            existing = self._rec_procs.get(cam_id)
            if existing and existing.poll() is None:
                return False
            self._recorder_intent[cam_id] = "wanted"

        cam_rec_dir = self._recordings_dir / cam_id
        cam_rec_dir.mkdir(parents=True, exist_ok=True)
        segments_log = cam_rec_dir / ".segments.log"

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            RTSP_TIMEOUT_US,
            "-i",
            rtsp_url,
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self._clip_duration),
            "-segment_format",
            "mp4",
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            # Append one line per completed segment. Segment muxer writes
            # the line only after closing the file, so "in the log" == "safe
            # to rename" is a strict invariant.
            "-segment_list",
            str(segments_log),
            "-segment_list_flags",
            "+live",
            "-segment_list_type",
            "flat",
            str(cam_rec_dir / "%Y%m%d_%H%M%S.mp4.part"),
        ]
        proc = self._launch_ffmpeg(cmd, f"rec-{cam_id}")
        if proc:
            with self._lock:
                self._rec_procs[cam_id] = proc
            self._start_finalizer(cam_id, cam_rec_dir, segments_log)
            log.info("Recorder started for %s (PID %d)", cam_id, proc.pid)
            return True
        return False

    # --- Recorder finalizer (rename .mp4.part → .mp4 on segment close) ----

    def _start_finalizer(self, cam_id, cam_rec_dir, segments_log):
        """Spawn (idempotently) the thread that renames completed segments.

        The thread polls the segment manifest at a short interval and
        renames each listed `.mp4.part` file to `.mp4`. Exits when the
        recorder stops OR the service is shut down.
        """
        with self._lock:
            existing = (
                self._finalizer_threads.get(cam_id)
                if hasattr(self, "_finalizer_threads")
                else None
            )
            if existing and existing.is_alive():
                return
            if not hasattr(self, "_finalizer_threads"):
                self._finalizer_threads = {}

        def _loop():
            last_offset = 0
            while self._running:
                try:
                    last_offset = finalize_completed_segments(
                        cam_rec_dir, segments_log, last_offset
                    )
                except Exception as exc:  # pragma: no cover — defensive
                    log.warning("Finalizer error for %s: %s", cam_id, exc)
                # Exit cleanly when the recorder has been stopped.
                with self._lock:
                    if self._recorder_intent.get(cam_id) == "stopped":
                        proc = self._rec_procs.get(cam_id)
                        if proc is None or proc.poll() is not None:
                            # One last sweep so we don't lose the final segment.
                            try:
                                finalize_completed_segments(
                                    cam_rec_dir, segments_log, last_offset
                                )
                            except Exception:  # pragma: no cover
                                pass
                            return
                for _ in range(10):
                    if not self._running:
                        return
                    time.sleep(0.1)

        t = threading.Thread(target=_loop, daemon=True, name=f"rec-finalizer-{cam_id}")
        with self._lock:
            self._finalizer_threads[cam_id] = t
        t.start()

    def stop_recorder(self, cam_id):
        """Deliberately stop the recorder for a camera."""
        with self._lock:
            self._recorder_intent[cam_id] = "stopped"
        self._stop_process(cam_id, self._rec_procs, "rec")

    def is_recording(self, cam_id) -> bool:
        """True iff the recorder ffmpeg for this camera is alive."""
        with self._lock:
            proc = self._rec_procs.get(cam_id)
            return proc is not None and proc.poll() is None

    # --- Watchdog ---------------------------------------------------------

    WATCHDOG_INTERVAL = 30  # seconds between health checks

    def _start_watchdog(self):
        """Background thread restarting only deliberately-wanted processes."""

        def _watchdog_loop():
            while self._running:
                try:
                    self._check_processes()
                except Exception as exc:
                    log.warning("Watchdog error: %s", exc)
                for _ in range(self.WATCHDOG_INTERVAL * 10):
                    if not self._running:
                        return
                    time.sleep(0.1)

        t = threading.Thread(target=_watchdog_loop, daemon=True, name="stream-watchdog")
        t.start()

    def _check_processes(self):
        """Restart snapshot/recorder processes that died unexpectedly.

        A process whose intent is "stopped" is left alone even if its Popen
        object is dead — the stop was deliberate.
        """
        with self._lock:
            snap_items = list(self._snap_procs.items())
            rec_items = list(self._rec_procs.items())

        for cam_id, proc in snap_items:
            if proc.poll() is None:
                continue
            intent = self._snap_intent.get(cam_id)
            if intent != "wanted":
                continue
            log.warning("Snapshot process died for %s, restarting", cam_id)
            self._close_proc_log(proc)
            with self._lock:
                self._snap_procs.pop(cam_id, None)
            self.start_snapshot(cam_id, f"{MEDIAMTX_URL}/{cam_id}")

        for cam_id, proc in rec_items:
            if proc.poll() is None:
                continue
            intent = self._recorder_intent.get(cam_id)
            if intent != "wanted":
                continue
            log.warning("Recorder died for %s, restarting", cam_id)
            self._close_proc_log(proc)
            with self._lock:
                self._rec_procs.pop(cam_id, None)
            self.start_recorder(cam_id, f"{MEDIAMTX_URL}/{cam_id}")

    # --- ffmpeg process plumbing ------------------------------------------

    def _launch_ffmpeg(self, cmd, label):
        """Launch an ffmpeg subprocess with stderr logged to a file."""
        try:
            stderr_dest = subprocess.PIPE
            log_file = None
            try:
                FFMPEG_LOG_DIR.mkdir(parents=True, exist_ok=True)
                log_path = FFMPEG_LOG_DIR / f"{label}.log"
                log_file = open(log_path, "a")
                stderr_dest = log_file
            except OSError:
                pass

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=stderr_dest,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
            proc._log_file = log_file  # type: ignore[attr-defined]
            return proc
        except FileNotFoundError:
            log.error("ffmpeg not found — cannot start %s", label)
        except OSError as e:
            log.error("Failed to start ffmpeg for %s: %s", label, e)
        return None

    @staticmethod
    def _close_proc_log(proc):
        log_file = getattr(proc, "_log_file", None)
        if log_file:
            try:
                log_file.close()
            except OSError:
                pass

    def _stop_process(self, cam_id, proc_dict, label):
        """Stop an ffmpeg process gracefully."""
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
                "%s stopped for %s (PID %d, exit=%s)",
                label,
                cam_id,
                proc.pid,
                proc.returncode,
            )
        except OSError:
            pass
        finally:
            self._close_proc_log(proc)


def create_recording_dirs(recordings_dir, cam_id):
    """Ensure <recordings_dir>/<cam_id>/ exists (flat layout under ADR-0017)."""
    path = Path(recordings_dir) / cam_id
    path.mkdir(parents=True, exist_ok=True)
    return path
