# REQ: SWR-001; RISK: RISK-002; SEC: SC-001; TEST: TC-004
"""Persistent login throttling for the server admin surface."""

from __future__ import annotations

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


class LoginRateLimiter:
    """File-backed IP login limiter shared by app instances.

    The JSON file is intentionally small and self-pruning. A sidecar lock file
    serializes Linux workers so normal service restarts and future multi-worker
    deployments keep one shared throttle budget.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        window_seconds: int,
        warn_after: int,
        block_after: int,
        clock=time.time,
    ):
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._window_seconds = window_seconds
        self._warn_after = warn_after
        self._block_after = block_after
        self._clock = clock
        self._thread_lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def check(self, ip: str) -> tuple[bool, bool]:
        """Return ``(allowed, warn)`` for the current IP budget."""
        key = self._key(ip)
        with self._locked_state() as state:
            attempts = self._pruned_attempts(state, key)
            state[key] = attempts
            count = len(attempts)
            if count >= self._block_after:
                return False, False
            if count >= self._warn_after:
                return True, True
            return True, False

    def record(self, ip: str) -> None:
        """Record one login attempt for ``ip``."""
        key = self._key(ip)
        with self._locked_state() as state:
            attempts = self._pruned_attempts(state, key)
            attempts.append(self._clock())
            state[key] = attempts[-self._block_after :]

    def clear(self) -> None:
        """Clear persisted state. Intended for tests and explicit maintenance."""
        with self._thread_lock:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def _locked_state(self) -> Iterator[dict[str, list[float]]]:
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

    def _read_state(self) -> dict[str, list[float]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        state: dict[str, list[float]] = {}
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, list):
                continue
            timestamps = [
                float(item) for item in value if isinstance(item, int | float)
            ]
            if timestamps:
                state[key] = timestamps
        return state

    def _write_state(self, state: dict[str, list[float]]) -> None:
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

    def _pruned_attempts(self, state: dict[str, list[float]], key: str) -> list[float]:
        now = self._clock()
        attempts = state.get(key, [])
        cutoff = now - self._window_seconds
        return [timestamp for timestamp in attempts if timestamp >= cutoff]

    @staticmethod
    def _key(ip: str) -> str:
        return ip or "unknown"
