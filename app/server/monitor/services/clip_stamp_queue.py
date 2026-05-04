# REQ: SWR-030; RISK: RISK-014, RISK-017; SEC: SC-014; TEST: TC-027
"""Per-camera bounded worker queues for clip timestamp stamping."""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from monitor.models import ServerMeta
from monitor.services.audit import CLIP_TIMESTAMP_REMUX_DROPPED

log = logging.getLogger("monitor.clip_stamp_queue")

QUEUE_MAXSIZE = 16


@dataclass
class _WorkerState:
    queue: queue.Queue
    stop_event: threading.Event
    thread: threading.Thread


class ClipStampQueue:
    """Own one stamping worker queue per camera."""

    def __init__(
        self,
        *,
        stamper,
        audit=None,
        camera_provider=None,
        server_meta_provider=None,
        maxsize: int = QUEUE_MAXSIZE,
    ):
        self._stamper = stamper
        self._audit = audit
        self._camera_provider = camera_provider or (lambda _camera_id: None)
        self._server_meta_provider = server_meta_provider or (lambda: ServerMeta())
        self._maxsize = int(maxsize)
        self._lock = threading.Lock()
        self._workers: dict[str, _WorkerState] = {}

    def enqueue(self, camera_id: str, clip_path: Path) -> None:
        """Queue a clip for stamping without blocking the recorder pipeline."""

        worker = self._ensure_worker(camera_id)
        item = Path(clip_path)
        try:
            worker.queue.put_nowait(item)
            return
        except queue.Full:
            pass

        dropped: Path | None = None
        try:
            dropped = worker.queue.get_nowait()
        except queue.Empty:
            pass
        try:
            worker.queue.put_nowait(item)
        except queue.Full:
            log.warning("clip_stamp_queue: queue remained full for %s", camera_id)
            return
        self._log_drop(camera_id, dropped)

    def shutdown(self) -> None:
        """Signal every worker to stop and wait for them to exit."""

        with self._lock:
            workers = list(self._workers.values())
            self._workers = {}
        for worker in workers:
            worker.stop_event.set()
            try:
                worker.queue.put_nowait(None)
            except queue.Full:
                pass
        for worker in workers:
            worker.thread.join(timeout=5)

    def _ensure_worker(self, camera_id: str) -> _WorkerState:
        with self._lock:
            worker = self._workers.get(camera_id)
            if worker and worker.thread.is_alive():
                return worker

            stop_event = threading.Event()
            q: queue.Queue = queue.Queue(maxsize=self._maxsize)
            thread = threading.Thread(
                target=self._run_worker,
                args=(camera_id, q, stop_event),
                daemon=True,
                name=f"clip-stamper-{camera_id}",
            )
            worker = _WorkerState(queue=q, stop_event=stop_event, thread=thread)
            self._workers[camera_id] = worker
            thread.start()
            return worker

    def _run_worker(
        self,
        camera_id: str,
        work_queue: queue.Queue,
        stop_event: threading.Event,
    ) -> None:
        while True:
            try:
                item = work_queue.get(timeout=0.25)
            except queue.Empty:
                if stop_event.is_set():
                    return
                continue
            if item is None:
                return
            try:
                camera = self._camera_provider(camera_id)
                server_meta = self._server_meta_provider() or ServerMeta()
                self._stamper.stamp(Path(item), camera, server_meta)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("clip_stamp_queue: worker error for %s: %s", camera_id, exc)

    def _log_drop(self, camera_id: str, dropped: Path | None) -> None:
        if self._audit is None:
            return
        filename = dropped.name if dropped else ""
        try:
            self._audit.log_event(
                CLIP_TIMESTAMP_REMUX_DROPPED,
                detail=f"camera_id={camera_id} filename={filename}".strip(),
            )
        except Exception:
            log.debug("clip_stamp_queue: audit log failed for dropped clip")
