"""
Motion-event store — append-only-with-cap persistence for motion events.

Events are persisted as a JSON array in ``/data/config/motion_events.json``
(ADR-0002 style). Capped at MAX_EVENTS globally; oldest 10 % are dropped
in batches when the cap is exceeded to avoid per-event full rewrites.

Thread-safe: a single lock guards both in-memory state and the file
write. Reads are served from memory; writes persist to disk atomically
(tempfile + os.replace).

See `docs/exec-plans/motion-detection.md`.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

from monitor.models import MotionEvent

log = logging.getLogger("monitor.motion_event_store")

MAX_EVENTS = 5000
COMPACT_DROP_FRACTION = 0.10  # when at cap, drop oldest 10 %


class MotionEventStore:
    """Persisted list of motion events, capped and append-ordered.

    Args:
        path: Path to the JSON file. Parent directory is created if
            missing. Does not need to exist on first construction.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._events: list[MotionEvent] = self._load()

    # --- Public API -------------------------------------------------------

    def append(self, event: MotionEvent) -> None:
        """Append a new event. Compacts automatically at cap."""
        with self._lock:
            # In-place update if this ID already exists (phase="end" arriving
            # after phase="start"). Events are expected to be short — linear
            # scan from the tail is fine for realistic volumes.
            for i, existing in enumerate(reversed(self._events)):
                if existing.id == event.id:
                    self._events[-(i + 1)] = event
                    self._persist()
                    return

            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                drop = max(1, int(MAX_EVENTS * COMPACT_DROP_FRACTION))
                self._events = self._events[drop:]
                log.info("motion_event_store compacted: dropped %d oldest events", drop)
            self._persist()

    def list_events(
        self,
        camera_id: str = "",
        limit: int = 100,
    ) -> list[MotionEvent]:
        """Return events, newest first. Optionally filtered by camera."""
        with self._lock:
            events = list(reversed(self._events))
        if camera_id:
            events = [e for e in events if e.camera_id == camera_id]
        return events[:limit]

    def get(self, event_id: str) -> MotionEvent | None:
        """Fetch a single event by ID, or None."""
        with self._lock:
            for evt in reversed(self._events):
                if evt.id == event_id:
                    return evt
        return None

    def attach_clip(self, event_id: str, clip_ref: dict) -> bool:
        """Attach a clip reference to an existing event.

        Called by the recordings correlator once an event's timestamp has
        been matched to a finalised clip on disk. Returns True on
        success, False if the event is unknown.
        """
        with self._lock:
            for i, evt in enumerate(reversed(self._events)):
                if evt.id == event_id:
                    evt.clip_ref = clip_ref
                    self._events[-(i + 1)] = evt
                    self._persist()
                    return True
        return False

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    # --- Internals --------------------------------------------------------

    def _load(self) -> list[MotionEvent]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("motion_event_store: load failed (%s), starting empty", exc)
            return []
        if not isinstance(raw, list):
            log.warning("motion_event_store: malformed root (not list), starting empty")
            return []
        events = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                events.append(MotionEvent(**item))
            except TypeError as exc:
                log.warning("motion_event_store: skipping malformed record: %s", exc)
        return events

    def _persist(self) -> None:
        """Atomic write: tempfile + os.replace. Caller holds the lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialised = [asdict(e) for e in self._events]
        fd, tmp = tempfile.mkstemp(
            prefix=".motion_events.", dir=self._path.parent, text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(serialised, f, separators=(",", ":"))
            os.chmod(tmp, 0o644)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
