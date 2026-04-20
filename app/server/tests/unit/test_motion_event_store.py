"""Unit tests for MotionEventStore."""

from __future__ import annotations

import json

import pytest

from monitor.models import MotionEvent
from monitor.services.motion_event_store import (
    COMPACT_DROP_FRACTION,
    MotionEventStore,
)


def _make_event(idx: int, camera_id: str = "cam-001") -> MotionEvent:
    return MotionEvent(
        id=f"mot-{idx:06d}-{camera_id}",
        camera_id=camera_id,
        started_at=f"2026-04-19T14:{idx % 60:02d}:00Z",
        ended_at=f"2026-04-19T14:{idx % 60:02d}:15Z",
        peak_score=0.1,
        peak_pixels_changed=5000,
        duration_seconds=15.0,
    )


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "motion_events.json"


class TestAppendAndList:
    def test_append_single_event_persists(self, store_path):
        store = MotionEventStore(store_path)
        store.append(_make_event(1))

        reloaded = MotionEventStore(store_path)
        assert reloaded.count() == 1
        events = reloaded.list_events()
        assert len(events) == 1
        assert events[0].id == "mot-000001-cam-001"

    def test_list_returns_newest_first(self, store_path):
        store = MotionEventStore(store_path)
        for i in range(1, 6):
            store.append(_make_event(i))

        events = store.list_events()
        assert [e.id for e in events] == [
            "mot-000005-cam-001",
            "mot-000004-cam-001",
            "mot-000003-cam-001",
            "mot-000002-cam-001",
            "mot-000001-cam-001",
        ]

    def test_list_limit_applied(self, store_path):
        store = MotionEventStore(store_path)
        for i in range(10):
            store.append(_make_event(i))

        events = store.list_events(limit=3)
        assert len(events) == 3

    def test_list_filters_by_camera(self, store_path):
        store = MotionEventStore(store_path)
        for i in range(5):
            store.append(_make_event(i, camera_id="cam-001"))
        for i in range(5, 8):
            store.append(_make_event(i, camera_id="cam-002"))

        events = store.list_events(camera_id="cam-002")
        assert len(events) == 3
        assert all(e.camera_id == "cam-002" for e in events)


class TestUpsertByID:
    def test_appending_same_id_replaces_existing(self, store_path):
        store = MotionEventStore(store_path)
        evt = _make_event(1)
        evt.ended_at = None  # simulate phase="start" arriving first
        store.append(evt)

        # phase="end" arrives and refreshes the record.
        evt_end = _make_event(1)
        evt_end.peak_score = 0.4
        store.append(evt_end)

        assert store.count() == 1
        retrieved = store.get("mot-000001-cam-001")
        assert retrieved is not None
        assert retrieved.peak_score == 0.4
        assert retrieved.ended_at is not None


class TestGet:
    def test_get_missing_returns_none(self, store_path):
        store = MotionEventStore(store_path)
        assert store.get("does-not-exist") is None

    def test_get_finds_event_in_middle(self, store_path):
        store = MotionEventStore(store_path)
        for i in range(5):
            store.append(_make_event(i))

        retrieved = store.get("mot-000002-cam-001")
        assert retrieved is not None
        assert retrieved.id == "mot-000002-cam-001"


class TestAttachClip:
    def test_attach_clip_persists(self, store_path):
        store = MotionEventStore(store_path)
        store.append(_make_event(1))

        ref = {
            "camera_id": "cam-001",
            "date": "2026-04-19",
            "filename": "20260419_142957.mp4",
            "offset_seconds": 5,
        }
        assert store.attach_clip("mot-000001-cam-001", ref) is True

        reloaded = MotionEventStore(store_path)
        retrieved = reloaded.get("mot-000001-cam-001")
        assert retrieved is not None
        assert retrieved.clip_ref == ref

    def test_attach_clip_unknown_id_returns_false(self, store_path):
        store = MotionEventStore(store_path)
        assert store.attach_clip("nope", {}) is False


class TestCompaction:
    def test_compacts_when_exceeding_cap(self, store_path, monkeypatch):
        # Speed up the test by lowering the cap to a friendly number.
        monkeypatch.setattr("monitor.services.motion_event_store.MAX_EVENTS", 50)
        store = MotionEventStore(store_path)

        # Fill past the cap.
        for i in range(60):
            store.append(_make_event(i))

        # Expect the cap minus the compaction drop.
        drop = max(1, int(50 * COMPACT_DROP_FRACTION))
        expected_floor = 50 - drop + 1  # +1 for the event that caused compaction
        assert store.count() <= 50
        assert store.count() >= expected_floor - 5  # allow slack

        # Oldest events must have been dropped.
        event_ids = {e.id for e in store.list_events(limit=1000)}
        assert "mot-000000-cam-001" not in event_ids
        assert "mot-000059-cam-001" in event_ids


class TestLoadCorruption:
    def test_missing_file_loads_empty(self, store_path):
        store = MotionEventStore(store_path)
        assert store.count() == 0

    def test_garbage_file_loads_empty_with_warning(self, store_path):
        store_path.write_text("{not json", encoding="utf-8")
        store = MotionEventStore(store_path)
        assert store.count() == 0

    def test_non_list_root_loads_empty(self, store_path):
        store_path.write_text('{"events": []}', encoding="utf-8")
        store = MotionEventStore(store_path)
        assert store.count() == 0

    def test_malformed_record_is_skipped(self, store_path):
        bad = [
            {"id": "ok-1", "camera_id": "cam-001", "started_at": "..."},
            {"bogus": "field"},
            "not a dict",
            {"id": "ok-2", "camera_id": "cam-001", "started_at": "..."},
        ]
        store_path.write_text(json.dumps(bad), encoding="utf-8")
        store = MotionEventStore(store_path)
        ids = [e.id for e in store.list_events()]
        assert "ok-1" in ids
        assert "ok-2" in ids
        assert store.count() == 2


class TestIsCameraActive:
    """RecordingScheduler consults this to decide if motion-mode cameras
    should be recording. Truth table: {no events, in-progress, recent end,
    old end, other camera}."""

    def _evt(self, store_path, idx, camera_id, ended_at):
        from monitor.services.motion_event_store import MotionEventStore

        store = MotionEventStore(store_path)
        evt = _make_event(idx, camera_id=camera_id)
        evt.ended_at = ended_at
        store.append(evt)
        return store

    def test_empty_store_returns_false(self, store_path):
        from monitor.services.motion_event_store import MotionEventStore

        store = MotionEventStore(store_path)
        assert store.is_camera_active("cam-001") is False

    def test_in_progress_event_returns_true(self, store_path):
        """Event with ``ended_at=None`` — start arrived, end not yet."""
        store = self._evt(store_path, 1, "cam-001", ended_at=None)
        assert store.is_camera_active("cam-001") is True

    def test_recent_end_within_post_roll_returns_true(self, store_path):
        from datetime import UTC, datetime, timedelta

        from monitor.services.motion_event_store import MotionEventStore

        # Event ended 5 s ago relative to our injected ``now``.
        now = datetime(2026, 4, 19, 14, 0, 30, tzinfo=UTC)
        ended = (now - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        store = MotionEventStore(store_path)
        evt = _make_event(1, camera_id="cam-001")
        evt.ended_at = ended
        store.append(evt)

        assert (
            store.is_camera_active("cam-001", post_roll_seconds=10.0, now=now) is True
        )

    def test_old_end_outside_post_roll_returns_false(self, store_path):
        from datetime import UTC, datetime, timedelta

        from monitor.services.motion_event_store import MotionEventStore

        # Event ended 30 s ago; post-roll is 10 s.
        now = datetime(2026, 4, 19, 14, 0, 30, tzinfo=UTC)
        ended = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        store = MotionEventStore(store_path)
        evt = _make_event(1, camera_id="cam-001")
        evt.ended_at = ended
        store.append(evt)

        assert (
            store.is_camera_active("cam-001", post_roll_seconds=10.0, now=now) is False
        )

    def test_different_camera_ignored(self, store_path):
        """Active event on cam-001 must not make cam-002 look active."""
        store = self._evt(store_path, 1, "cam-001", ended_at=None)
        assert store.is_camera_active("cam-002") is False

    def test_malformed_ended_at_is_skipped(self, store_path):
        """Garbage timestamp must not crash the scheduler tick."""
        from monitor.services.motion_event_store import MotionEventStore

        store = MotionEventStore(store_path)
        evt = _make_event(1, camera_id="cam-001")
        evt.ended_at = "not-a-timestamp"
        store.append(evt)

        # Unparseable end → treated as "can't confirm active".
        assert store.is_camera_active("cam-001") is False

    def test_most_recent_event_wins(self, store_path):
        """Old ended event in store but a newer in-progress one → active."""
        from monitor.services.motion_event_store import MotionEventStore

        store = MotionEventStore(store_path)

        # Older event: ended long ago.
        old = _make_event(1, camera_id="cam-001")
        old.ended_at = "2020-01-01T00:00:00Z"
        store.append(old)

        # Newer event: still in progress.
        fresh = _make_event(2, camera_id="cam-001")
        fresh.ended_at = None
        store.append(fresh)

        assert store.is_camera_active("cam-001") is True


class TestAtomicWrite:
    def test_write_does_not_leave_tempfiles(self, store_path):
        store = MotionEventStore(store_path)
        for i in range(3):
            store.append(_make_event(i))

        # No .motion_events.* tempfiles left in the directory.
        tempfiles = list(store_path.parent.glob(".motion_events.*"))
        assert tempfiles == []

    def test_persist_uses_atomic_replace(self, store_path):
        """Write is atomic even if interrupted — verify the final state."""
        store = MotionEventStore(store_path)
        store.append(_make_event(1))

        # The file should exist + be valid JSON immediately.
        content = json.loads(store_path.read_text(encoding="utf-8"))
        assert isinstance(content, list)
        assert len(content) == 1


class TestAutoCloseOnNewStart:
    """A new "start" from camera X auto-closes any orphan open event
    for that same camera — recovers cleanly from camera restarts /
    crashes / network blips that dropped the matching "end" POST."""

    def _start(self, idx, camera_id, ts):
        return MotionEvent(
            id=f"mot-{idx:06d}-{camera_id}",
            camera_id=camera_id,
            started_at=ts,
            ended_at=None,  # in-progress
            peak_score=0.1,
            peak_pixels_changed=100,
            duration_seconds=0.0,
        )

    def test_new_start_closes_prior_orphan_same_camera(self, store_path):
        store = MotionEventStore(store_path)
        store.append(self._start(1, "cam-A", "2026-04-20T06:50:00Z"))
        # Two minutes later, a fresh start arrives without the first
        # event having sent an "end". The store should force-close #1.
        store.append(self._start(2, "cam-A", "2026-04-20T06:52:00Z"))

        events = store.list_events()
        # Newest first: the new start is still open; the prior one is closed.
        assert events[0].id.endswith("000002-cam-A")
        assert events[0].ended_at is None

        assert events[1].id.endswith("000001-cam-A")
        assert events[1].ended_at == "2026-04-20T06:52:00Z"
        assert events[1].duration_seconds == 120.0

    def test_new_start_does_not_touch_other_camera_orphans(self, store_path):
        store = MotionEventStore(store_path)
        store.append(self._start(1, "cam-A", "2026-04-20T06:50:00Z"))
        store.append(self._start(2, "cam-B", "2026-04-20T06:52:00Z"))

        events = {e.id: e for e in store.list_events()}
        # cam-A's event is still open — the new start was for a different camera.
        assert events["mot-000001-cam-A"].ended_at is None
        assert events["mot-000002-cam-B"].ended_at is None

    def test_reap_stale_closes_old_open_events(self, store_path):
        from datetime import datetime, timezone

        store = MotionEventStore(store_path)
        store.append(self._start(1, "cam-A", "2026-04-20T06:00:00Z"))
        # "Now" is 11 minutes later — past the 10-min default threshold.
        now = datetime(2026, 4, 20, 6, 11, 0, tzinfo=timezone.utc)
        closed = store.reap_stale(now=now, max_age_seconds=600.0)
        assert closed == 1
        e = store.list_events()[0]
        assert e.ended_at == "2026-04-20T06:00:00Z"
        assert e.duration_seconds == 0.0

    def test_reap_stale_skips_fresh_open_events(self, store_path):
        from datetime import datetime, timezone

        store = MotionEventStore(store_path)
        store.append(self._start(1, "cam-A", "2026-04-20T06:00:00Z"))
        now = datetime(2026, 4, 20, 6, 0, 30, tzinfo=timezone.utc)  # 30 s later
        assert store.reap_stale(now=now, max_age_seconds=600.0) == 0
        assert store.list_events()[0].ended_at is None
