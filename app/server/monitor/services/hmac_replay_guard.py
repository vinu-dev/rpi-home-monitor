# REQ: SWR-003, SWR-004; RISK: RISK-002, RISK-005; SEC: SC-002; TEST: TC-012
"""Persistent replay guard for camera-to-server HMAC requests."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:  # pragma: no cover - exercised on Linux hardware/CI paths.
    import fcntl
except ImportError:  # pragma: no cover - Windows test host fallback.
    fcntl = None


class HmacReplayGuard:
    """File-backed replay cache shared by Flask workers and restarts."""

    def __init__(
        self,
        path: str | Path,
        *,
        ttl_seconds: int,
        clock=time.time,
    ):
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._thread_lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record_and_check(self, camera_id: str, timestamp: str, signature: str) -> bool:
        """Record a signed request and return True when it is a replay."""
        key = self._key(camera_id, timestamp, signature)
        now = self._clock()
        expires_at = now + self._ttl_seconds

        with self._locked_state() as state:
            camera_cache = self._pruned_camera_cache(state, camera_id, now)
            if key in camera_cache:
                return True
            camera_cache[key] = expires_at
            state[camera_id] = camera_cache
            return False

    def clear(self) -> None:
        """Clear persisted state. Intended for tests and explicit maintenance."""
        with self._thread_lock:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, dict[str, float]]]:
        with self._thread_lock:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock_path.open("a+") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    state = self._read_state()
                    yield state
                    self._write_state(state)
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_state(self) -> dict[str, dict[str, float]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}

        state: dict[str, dict[str, float]] = {}
        for camera_id, cache in raw.items():
            if not isinstance(camera_id, str) or not isinstance(cache, dict):
                continue
            clean_cache: dict[str, float] = {}
            for key, expires_at in cache.items():
                if isinstance(key, str) and isinstance(expires_at, int | float):
                    clean_cache[key] = float(expires_at)
            if clean_cache:
                state[camera_id] = clean_cache
        return state

    def _write_state(self, state: dict[str, dict[str, float]]) -> None:
        empty_cameras = [camera_id for camera_id, cache in state.items() if not cache]
        for camera_id in empty_cameras:
            del state[camera_id]

        if not state:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
            return

        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(state, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp_path, self._path)

    def _pruned_camera_cache(
        self,
        state: dict[str, dict[str, float]],
        camera_id: str,
        now: float,
    ) -> dict[str, float]:
        cache = state.get(camera_id, {})
        return {
            key: expires_at for key, expires_at in cache.items() if expires_at > now
        }

    @staticmethod
    def _key(camera_id: str, timestamp: str, signature: str) -> str:
        material = f"{camera_id}:{timestamp}:{signature}".encode()
        return hashlib.sha256(material).hexdigest()
