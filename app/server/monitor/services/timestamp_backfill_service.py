# REQ: SWR-024, SWR-029; RISK: RISK-012, RISK-014; SEC: SC-012, SC-014, SC-020; TEST: TC-023, TC-026
"""Background backfill for timestamp-stamping older recordings."""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from monitor.models import ServerMeta
from monitor.services.audit import (
    CLIP_TIMESTAMP_BACKFILL_CANCELLED,
    CLIP_TIMESTAMP_BACKFILL_COMPLETED,
    CLIP_TIMESTAMP_BACKFILL_STARTED,
)
from monitor.services.clip_stamper import stamp_sentinel_path

log = logging.getLogger("monitor.timestamp_backfill")

_CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TimestampBackfillService:
    """Run a cancellable, single-threaded backfill over unstamped clips."""

    def __init__(
        self,
        *,
        recordings_dir: str | Path,
        stamper,
        store,
        audit=None,
        server_meta_provider=None,
        throttle_seconds: float = 0.1,
    ):
        self._recordings_dir = Path(recordings_dir)
        self._stamper = stamper
        self._store = store
        self._audit = audit
        self._server_meta_provider = server_meta_provider or (lambda: ServerMeta())
        self._throttle_seconds = float(throttle_seconds)
        self._lock = threading.Lock()
        self._state = "idle"
        self._processed = 0
        self._total = 0
        self._current_camera = ""
        self._started_at = ""
        self._thread: threading.Thread | None = None
        self._cancel_requested = False

    def start(self) -> tuple[dict, int]:
        """Start a backfill run if one is not already active."""

        with self._lock:
            if self._state in {"running", "cancelling"}:
                return {"error": "Backfill already in progress"}, 409
            inventory = self._unstamped_inventory()
            self._processed = 0
            self._total = len(inventory)
            self._current_camera = ""
            self._started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._state = "running"
            self._cancel_requested = False
            self._thread = threading.Thread(
                target=self._run,
                args=(inventory,),
                daemon=True,
                name="timestamp-backfill",
            )
            self._thread.start()
        self._log_audit(
            CLIP_TIMESTAMP_BACKFILL_STARTED,
            detail=f"clip_count={len(inventory)} camera_count={len({c for c, _ in inventory})}",
        )
        return self.get_status(), 202

    def cancel(self) -> tuple[dict, int]:
        """Request cancellation at the next clip boundary."""

        with self._lock:
            if self._state == "idle":
                status = 200
            else:
                self._cancel_requested = True
                self._state = "cancelling"
                status = 202
        return self.get_status(), status

    def get_status(self) -> dict:
        """Return current backfill state plus stamped/unstamped counts."""

        with self._lock:
            state = self._state
            processed = self._processed
            total = self._total
            current_camera = self._current_camera
            started_at = self._started_at
        summary = self._timestamp_summary()
        return {
            "state": state,
            "processed": processed,
            "total": total,
            "current_camera": current_camera,
            "started_at": started_at,
            "ffmpeg_available": self._stamper.tools_available(),
            "summary": summary,
        }

    def set_recordings_dir(self, recordings_dir: str | Path) -> None:
        self._recordings_dir = Path(recordings_dir)

    def _run(self, inventory: list[tuple[str, Path]]) -> None:
        failures = 0
        for index, (camera_id, clip_path) in enumerate(inventory, start=1):
            with self._lock:
                self._current_camera = camera_id
                cancel_requested = self._cancel_requested
            if cancel_requested:
                break
            try:
                camera = self._store.get_camera(camera_id)
                result = self._stamper.stamp(
                    clip_path,
                    camera,
                    self._server_meta_provider() or ServerMeta(),
                )
                if not result.ok:
                    failures += 1
            except Exception as exc:  # pragma: no cover - defensive
                failures += 1
                log.warning("timestamp_backfill: failed for %s: %s", clip_path, exc)
            with self._lock:
                self._processed = index
            if self._throttle_seconds > 0 and index < len(inventory):
                time.sleep(self._throttle_seconds)

        with self._lock:
            cancelled = self._cancel_requested
            processed = self._processed
            total = self._total
            self._state = "idle"
            self._current_camera = ""
            self._cancel_requested = False
        event = (
            CLIP_TIMESTAMP_BACKFILL_CANCELLED
            if cancelled
            else CLIP_TIMESTAMP_BACKFILL_COMPLETED
        )
        self._log_audit(
            event,
            detail=(
                f"processed={processed} total={total} "
                f"fail_count={failures} cancelled={str(cancelled).lower()}"
            ),
        )

    def _unstamped_inventory(self) -> list[tuple[str, Path]]:
        clips: list[tuple[str, Path]] = []
        for row in self._timestamp_summary()["cameras"]:
            camera_id = row["camera_id"]
            cam_dir = self._recordings_dir / camera_id
            for clip_path in sorted(cam_dir.rglob("*.mp4")):
                if stamp_sentinel_path(clip_path).exists():
                    continue
                clips.append((camera_id, clip_path))
        return clips

    def _timestamp_summary(self) -> dict:
        cameras: list[dict] = []
        stamped_total = 0
        unstamped_total = 0
        if not self._recordings_dir.is_dir():
            return {"stamped": 0, "unstamped": 0, "cameras": cameras}

        for cam_dir in sorted(self._recordings_dir.iterdir()):
            if not cam_dir.is_dir() or not _CAMERA_ID_RE.match(cam_dir.name):
                continue
            stamped = 0
            unstamped = 0
            for clip_path in cam_dir.rglob("*.mp4"):
                if stamp_sentinel_path(clip_path).exists():
                    stamped += 1
                else:
                    unstamped += 1
            if stamped == 0 and unstamped == 0:
                continue
            camera = self._store.get_camera(cam_dir.name)
            cameras.append(
                {
                    "camera_id": cam_dir.name,
                    "camera_name": getattr(camera, "name", "") or cam_dir.name,
                    "stamped": stamped,
                    "unstamped": unstamped,
                }
            )
            stamped_total += stamped
            unstamped_total += unstamped
        return {
            "stamped": stamped_total,
            "unstamped": unstamped_total,
            "cameras": cameras,
        }

    def _log_audit(self, event: str, *, detail: str) -> None:
        if self._audit is None:
            return
        try:
            self._audit.log_event(event, detail=detail)
        except Exception:
            log.debug("timestamp_backfill: audit log failed for %s", event)
