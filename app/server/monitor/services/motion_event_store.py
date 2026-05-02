# REQ: SWR-008; RISK: RISK-005; SEC: SC-015; TEST: TC-019
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
from datetime import UTC, datetime
from pathlib import Path

from monitor.models import MotionEvent

log = logging.getLogger("monitor.motion_event_store")

MAX_EVENTS = 5000
COMPACT_DROP_FRACTION = 0.10  # when at cap, drop oldest 10 %


def _parse_iso_z(iso: str) -> datetime | None:
    """Parse an ISO-8601 ``...Z`` timestamp into a UTC-aware datetime."""
    if not iso:
        return None
    try:
        normalised = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(normalised).astimezone(UTC)
    except (ValueError, TypeError):
        return None


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
        """Append a new event. Compacts automatically at cap.

        Side effect: if ``event`` is a brand-new start for ``camera_id``
        (i.e. its ID isn't already on file and ``ended_at is None``),
        any still-open event from the SAME camera is force-closed with
        ``ended_at = event.started_at``. This keeps the UI honest when
        the camera restarts / crashes / loses network mid-event and
        never sends the matching "end" POST — the next motion start from
        that camera reaps the orphan rather than leaving it "ongoing"
        forever.
        """
        with self._lock:
            # In-place update if this ID already exists (phase="end" arriving
            # after phase="start"). Events are expected to be short — linear
            # scan from the tail is fine for realistic volumes.
            for i, existing in enumerate(reversed(self._events)):
                if existing.id == event.id:
                    self._events[-(i + 1)] = event
                    self._persist()
                    return

            # New event. If it's a "start" (no end yet), close any prior
            # open events for the same camera — see method docstring.
            if event.ended_at is None or event.ended_at == "":
                closed = 0
                for i, existing in enumerate(self._events):
                    if existing.camera_id == event.camera_id and (
                        existing.ended_at is None or existing.ended_at == ""
                    ):
                        existing.ended_at = event.started_at
                        # Duration from the original start to the forced close.
                        start_dt = _parse_iso_z(existing.started_at)
                        end_dt = _parse_iso_z(event.started_at)
                        if start_dt and end_dt:
                            existing.duration_seconds = round(
                                (end_dt - start_dt).total_seconds(), 2
                            )
                        self._events[i] = existing
                        closed += 1
                if closed:
                    log.info(
                        "motion_event_store: auto-closed %d orphaned event(s) for camera=%s on new start",
                        closed,
                        event.camera_id,
                    )

            self._events.append(event)
            if len(self._events) > MAX_EVENTS:
                drop = max(1, int(MAX_EVENTS * COMPACT_DROP_FRACTION))
                self._events = self._events[drop:]
                log.info("motion_event_store compacted: dropped %d oldest events", drop)
            self._persist()

    def reap_stale(
        self, now: datetime | None = None, max_age_seconds: float = 600.0
    ) -> int:
        """Close any open event older than ``max_age_seconds``.

        Fallback for the case where a camera starts a motion event and
        then goes offline — without this, the row stays "ongoing" until
        the same camera comes back and fires a fresh start (which
        triggers the auto-close in ``append``). Call periodically from
        a watchdog.

        Returns the number of events closed.
        """
        if now is None:
            now = datetime.now(UTC)
        closed = 0
        with self._lock:
            for i, evt in enumerate(self._events):
                if evt.ended_at not in (None, ""):
                    continue
                start_dt = _parse_iso_z(evt.started_at)
                if start_dt is None:
                    continue
                age = (now - start_dt).total_seconds()
                if age < max_age_seconds:
                    continue
                evt.ended_at = evt.started_at  # zero-duration sentinel
                evt.duration_seconds = 0.0
                self._events[i] = evt
                closed += 1
            if closed:
                self._persist()
                log.info("motion_event_store.reap_stale closed %d orphan(s)", closed)
        return closed

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

    def is_camera_active(
        self,
        camera_id: str,
        post_roll_seconds: float = 10.0,
        now: datetime | None = None,
    ) -> bool:
        """True iff the camera is currently in a motion window.

        Definition of "in a window":
          * Any event for this camera with ``ended_at is None`` (the start
            has arrived but the end hasn't) → in progress.
          * Any event whose ``ended_at`` is within ``post_roll_seconds`` of
            ``now`` → still in the post-roll grace period.

        Used by ``RecordingScheduler`` to decide whether motion-mode
        cameras should have their recorder running this tick. See
        docs/exec-plans/motion-detection.md §Phase 4.

        Args:
            camera_id: Which camera to test.
            post_roll_seconds: How long after the last event end to keep
                the window open. Lets the recorder keep going for a few
                seconds after the motion actually stopped, so the saved
                clip contains the aftermath the user wants to see.
            now: Injectable clock for tests. Defaults to
                ``datetime.now(UTC)``.
        """
        ref = now or datetime.now(UTC)
        with self._lock:
            # Scan tail-first — the most recent event is almost always
            # the relevant one for "is this camera active now?".
            for evt in reversed(self._events):
                if evt.camera_id != camera_id:
                    continue
                if evt.ended_at is None or evt.ended_at == "":
                    # Start-only event with no end yet: in progress.
                    return True
                end_dt = _parse_iso_z(evt.ended_at)
                if end_dt is None:
                    continue
                # Events are inserted in chronological order; once we
                # find an event for this camera whose end is older than
                # the post-roll window, no earlier event can satisfy the
                # check either — return directly rather than keep looping.
                return (ref - end_dt).total_seconds() <= post_roll_seconds
        return False

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
