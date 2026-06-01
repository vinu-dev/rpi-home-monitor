# REQ: SWR-072; RISK: RISK-001, RISK-015; SEC: SC-001, SC-002, SC-008; TEST: TC-057
"""Camera scheduled/manual restart helpers."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from camera_streamer import privileged
from camera_streamer.restart_schedule_model import scheduled_minute_key

log = logging.getLogger("camera-streamer.restart-scheduler")


def request_reboot_async(reason: str = "manual", delay_seconds: float = 1.0) -> None:
    """Request a system reboot after the HTTP response has time to flush."""

    def _worker() -> None:
        time.sleep(delay_seconds)
        log.warning("Requesting camera reboot (%s)", reason)
        try:
            if privileged.should_use_helper():
                privileged.request("system.reboot", timeout=15)
            else:
                subprocess.run(["systemctl", "reboot"], check=False, timeout=15)
        except privileged.PrivilegedHelperError as exc:
            log.error("Privileged reboot request failed: %s", exc)
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("System reboot command failed: %s", exc)

    threading.Thread(target=_worker, daemon=True, name="camera-reboot").start()


class CameraRestartScheduler:
    """Runs the camera's local maintenance restart schedule."""

    def __init__(
        self,
        config,
        *,
        check_interval_seconds: int = 20,
        is_blocked: Callable[[], bool] | None = None,
    ):
        self._config = config
        self._check_interval = check_interval_seconds
        self._is_blocked = is_blocked or (lambda: False)
        self._state_path = Path(config.config_dir) / "restart_schedule_state.json"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_runtime_key = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="restart-schedule",
        )
        self._thread.start()
        log.info("Camera restart scheduler started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._check_interval + 2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception as exc:
                log.warning("Camera restart schedule check failed: %s", exc)
            self._stop_event.wait(self._check_interval)

    def check_once(self, *, now=None) -> bool:
        now_dt = now.astimezone() if now is not None else datetime.now().astimezone()
        key = scheduled_minute_key(now_dt, self._config.restart_schedule)
        if not key:
            return False
        if key == self._last_runtime_key or self._has_run(key):
            return False
        if self._is_blocked():
            log.info("Skipping scheduled camera reboot; OTA is busy")
            self._mark_run(key)
            return False
        self._mark_run(key)
        self._last_runtime_key = key
        request_reboot_async("scheduled")
        return True

    def _state(self) -> dict:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, data: dict) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _has_run(self, key: str) -> bool:
        return self._state().get("camera") == key

    def _mark_run(self, key: str) -> None:
        state = self._state()
        state["camera"] = key
        self._write_state(state)
