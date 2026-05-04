# REQ: SWR-030; RISK: RISK-014, RISK-017; SEC: SC-014; TEST: TC-027
"""Unit tests for the per-camera clip stamp queue."""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from monitor.models import Camera, ServerMeta
from monitor.services.clip_stamp_queue import ClipStampQueue


def test_enqueue_dispatches_clip_to_worker(tmp_path):
    clip_path = tmp_path / "20260420_140000.mp4"
    clip_path.write_bytes(b"x")
    stamper = MagicMock()
    stamp_queue = ClipStampQueue(
        stamper=stamper,
        camera_provider=lambda camera_id: Camera(id=camera_id, name="Front Door"),
        server_meta_provider=lambda: ServerMeta(hostname="home-monitor"),
    )

    stamp_queue.enqueue("cam-001", clip_path)

    deadline = time.time() + 2
    while time.time() < deadline and not stamper.stamp.called:
        time.sleep(0.05)

    stamp_queue.shutdown()

    assert stamper.stamp.called
    call = stamper.stamp.call_args
    assert call.args[0] == clip_path
    assert call.args[1].id == "cam-001"


def test_enqueue_drops_oldest_when_queue_is_full():
    q = queue.Queue(maxsize=1)
    q.put(Path("old.mp4"))
    worker = SimpleNamespace(
        queue=q,
        stop_event=threading.Event(),
        thread=SimpleNamespace(join=lambda timeout=None: None),
    )
    audit = MagicMock()
    stamp_queue = ClipStampQueue(
        stamper=MagicMock(),
        audit=audit,
        camera_provider=lambda _camera_id: None,
        server_meta_provider=lambda: ServerMeta(),
        maxsize=1,
    )

    with patch.object(stamp_queue, "_ensure_worker", return_value=worker):
        stamp_queue.enqueue("cam-001", Path("new.mp4"))

    assert q.get_nowait() == Path("new.mp4")
    audit.log_event.assert_called_once()
    assert audit.log_event.call_args.args[0] == "CLIP_TIMESTAMP_REMUX_DROPPED"
