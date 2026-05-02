# REQ: SWR-005, SWR-006; RISK: RISK-001; SEC: SC-002; TEST: TC-002, TC-001
"""
Loop recorder (ADR-0017) — keeps the recordings volume under its watermark
by deleting the oldest completed segments when free space runs low.

Policy:
    free_percent < low_watermark      → delete oldest until
    free_percent >= low_watermark + hysteresis.

Safeguards:
    - Never touches the currently-writing segment. A file is "live" when its
      mtime is within `live_age_seconds` (default 600 = 10 minutes) or it
      appears in the `live_segments` callback from the scheduler.
    - Only considers `.mp4` files under <base_dir>/<camera_id>/.
    - Every deletion emits a `RECORDING_ROTATED` audit event.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("monitor.loop_recorder")

TICK_INTERVAL_SECONDS = 60
DEFAULT_LOW_WATERMARK = 10  # percent free
DEFAULT_HYSTERESIS = 5  # extra percent above low watermark to reclaim
DEFAULT_LIVE_AGE = 600  # seconds

# Storage-low alert threshold (#140). Emits STORAGE_LOW when free
# space drops below this percent — a heads-up *before* the auto-FIFO
# cleanup at low_watermark kicks in. Default 5% above low_watermark
# so on the shipping (10/5) defaults the alert fires at <15% free,
# right when cleanup is starting to be likely.
DEFAULT_STORAGE_LOW_HEADROOM = 5


class LoopRecorder:
    """Daemon that prunes oldest segments when disk is low."""

    def __init__(
        self,
        base_dir: str | Path,
        audit=None,
        low_watermark: int = DEFAULT_LOW_WATERMARK,
        hysteresis: int = DEFAULT_HYSTERESIS,
        live_age_seconds: int = DEFAULT_LIVE_AGE,
        live_segments_getter: Callable[[], set] | None = None,
        tick_seconds: int = TICK_INTERVAL_SECONDS,
        storage_low_headroom: int = DEFAULT_STORAGE_LOW_HEADROOM,
    ):
        self._base_dir = Path(base_dir)
        self._audit = audit
        self._low = low_watermark
        self._hys = hysteresis
        self._low_headroom = storage_low_headroom
        self._live_age = live_age_seconds
        self._live_segments_getter = live_segments_getter
        self._tick = tick_seconds
        self._running = False
        self._thread: threading.Thread | None = None
        # Edge-detection state for #140 alerts. In-memory deliberately:
        # if the server restarts with disk already in trouble we WANT
        # to re-emit the alert as a fresh signal, so persistence here
        # would harm rather than help (opposite of the per-camera
        # offline-alert state in #136 which had to span restarts).
        self._storage_low_active = False
        self._retention_risk_active = False

    # --- Lifecycle --------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="loop-recorder", daemon=True
        )
        self._thread.start()
        log.info(
            "LoopRecorder started (watermark=%d%%, hysteresis=%d%%)",
            self._low,
            self._hys,
        )

    def stop(self):
        self._running = False

    def set_watermarks(self, low: int, hysteresis: int) -> None:
        """Update watermark thresholds at runtime.

        Called by SettingsService when the admin changes the loop settings
        so the running recorder reflects the new values without a restart.
        """
        self._low = int(low)
        self._hys = int(hysteresis)
        log.info(
            "LoopRecorder watermarks updated (low=%d%%, hysteresis=%d%%)",
            self._low,
            self._hys,
        )

    def set_base_dir(self, new_dir: str | Path) -> None:
        """Redirect the recorder to a new recordings root (e.g. after USB mount).

        Must be called whenever the recordings directory changes so that
        free-space checks and segment enumeration target the right filesystem.
        Without this, the recorder watches the old path (typically the internal
        /data partition which is nearly empty) and never triggers cleanup on the
        USB drive that is actually full.
        """
        self._base_dir = Path(new_dir)
        log.info("LoopRecorder base_dir updated: %s", self._base_dir)

    # --- Public API -------------------------------------------------------

    def tick(self) -> int:
        """Run one reclamation pass. Returns the number of files deleted."""
        try:
            free_pct = self._free_percent()
        except OSError as exc:
            log.warning("LoopRecorder: statvfs failed: %s", exc)
            return 0

        # Edge-detected STORAGE_LOW (#140). Fires once per false→true
        # transition of "free space below the headroom warning level".
        # Recovery (true→false) flips the flag silently — no "all
        # clear" audit per ADR-0024 (CAMERA_ONLINE-style recovery
        # codes are intentionally NOT in the alert catalogue).
        storage_low_threshold = self._low + self._low_headroom
        is_low_now = free_pct < storage_low_threshold
        if is_low_now and not self._storage_low_active:
            self._emit_audit(
                "STORAGE_LOW",
                f"recordings free space {free_pct:.1f}% < {storage_low_threshold}%",
            )
        self._storage_low_active = is_low_now

        if free_pct >= self._low:
            # Above the cleanup watermark — clear retention-risk and
            # short-circuit the deletion loop.
            self._retention_risk_active = False
            return 0

        target = self._low + self._hys
        candidates = self._segments_oldest_first()
        live = self._live_segments()
        now = time.time()
        deleted = 0

        for path in candidates:
            if self._free_percent() >= target:
                break
            if self._is_live(path, live, now):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            try:
                path.unlink()
            except OSError as exc:
                log.warning("LoopRecorder: delete %s failed: %s", path, exc)
                continue
            deleted += 1
            self._audit_delete(path, size)

        # Edge-detected RETENTION_RISK (#140). The recorder is actively
        # auto-deleting clips because we crossed the low watermark; the
        # user's expected retention is being violated by FIFO. One alert
        # per crossing, not per deletion — re-arms once we recover.
        if deleted > 0 and not self._retention_risk_active:
            try:
                free_after = self._free_percent()
            except OSError:
                free_after = free_pct
            self._emit_audit(
                "RETENTION_RISK",
                (
                    f"auto-deleting recordings to recover space "
                    f"({deleted} clips this pass; free now {free_after:.1f}%)"
                ),
            )
            self._retention_risk_active = True

        return deleted

    # --- Internals --------------------------------------------------------

    def _run(self):
        while self._running:
            try:
                self.tick()
            except Exception as exc:
                log.warning("LoopRecorder tick error: %s", exc)
            for _ in range(self._tick * 10):
                if not self._running:
                    return
                time.sleep(0.1)

    def _free_percent(self) -> float:
        """Return free-space percentage for the base directory's filesystem."""
        if not self._base_dir.exists():
            return 100.0
        usage = shutil.disk_usage(str(self._base_dir))
        if usage.total == 0:
            return 100.0
        return 100.0 * usage.free / usage.total

    def _segments_oldest_first(self) -> list[Path]:
        """All .mp4 segments under base_dir, sorted by mtime ascending."""
        if not self._base_dir.is_dir():
            return []
        files: list[Path] = []
        for p in self._base_dir.rglob("*.mp4"):
            if p.is_file():
                files.append(p)
        files.sort(key=lambda p: _safe_mtime(p))
        return files

    def _live_segments(self) -> set:
        if self._live_segments_getter is None:
            return set()
        try:
            return set(self._live_segments_getter() or ())
        except Exception:
            return set()

    def _is_live(self, path: Path, live: set, now: float) -> bool:
        if str(path) in live or path in live:
            return True
        try:
            age = now - path.stat().st_mtime
        except OSError:
            return True  # safer to keep
        return age < self._live_age

    def _emit_audit(self, event: str, detail: str) -> None:
        """Best-effort audit emission for storage health events (#140).

        Defensive: a logging failure must not break the cleanup loop.
        Logs at DEBUG so a missing audit logger doesn't spam INFO.
        """
        if self._audit is None:
            return
        try:
            self._audit.log_event(event, user="system", ip="", detail=detail)
        except Exception as exc:
            log.debug("LoopRecorder audit emit %s failed: %s", event, exc)

    def _audit_delete(self, path: Path, size: int):
        if self._audit is None:
            return
        try:
            self._audit.log_event(
                "RECORDING_ROTATED",
                user="system",
                ip="",
                detail=f"deleted {path} ({size} bytes)",
            )
        except Exception as exc:
            log.debug("LoopRecorder audit log failed: %s", exc)


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0
