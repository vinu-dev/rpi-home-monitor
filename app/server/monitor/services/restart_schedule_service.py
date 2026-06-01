# REQ: SWR-072; RISK: RISK-001, RISK-015; SEC: SC-001, SC-002, SC-008; TEST: TC-057
"""Scheduled and manual restart service for the server appliance."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from monitor.services import privileged
from monitor.services.restart_schedule_model import (
    next_run_at,
    normalise_restart_schedule,
    scheduled_minute_key,
    timezone_or_utc,
)

log = logging.getLogger("monitor.services.restart_schedule")


class RestartScheduleService:
    """Owns server scheduled restarts and manual reboot requests."""

    def __init__(
        self,
        *,
        store,
        config_dir: str,
        audit=None,
        check_interval_seconds: int = 20,
        reboot_delay_seconds: float = 1.0,
        is_blocked=None,
    ):
        self._store = store
        self._audit = audit
        self._state_path = Path(config_dir) / "restart_schedule_state.json"
        self._check_interval = check_interval_seconds
        self._reboot_delay = reboot_delay_seconds
        self._is_blocked = is_blocked or (lambda: False)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_runtime_key = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="restart-schedule",
            daemon=True,
        )
        self._thread.start()
        log.info("Restart schedule service started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._check_interval + 2)
            self._thread = None

    def server_schedule_status(self) -> dict:
        settings = self._store.get_settings()
        schedule = normalise_restart_schedule(
            getattr(settings, "restart_schedule", {}),
            source="server",
            stamp=False,
        )
        schedule["next_run_at"] = next_run_at(schedule, settings.timezone)
        return schedule

    def update_server_schedule(
        self,
        payload: dict,
        *,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[dict, str, int]:
        settings = self._store.get_settings()
        payload = (
            {**payload, "source": "server"} if isinstance(payload, dict) else payload
        )
        schedule = normalise_restart_schedule(payload, source="server", stamp=True)
        settings.restart_schedule = schedule
        self._store.save_settings(settings)
        self._log_audit(
            "SERVER_RESTART_SCHEDULE_UPDATED",
            requesting_user,
            requesting_ip,
            f"enabled={schedule['enabled']} days={','.join(schedule['days'])} time={schedule['time']}",
        )
        schedule["next_run_at"] = next_run_at(schedule, settings.timezone)
        return schedule, "", 200

    def request_server_reboot(
        self,
        *,
        reason: str = "manual",
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        detail = f"reason={reason}"
        self._log_audit(
            "SERVER_REBOOT_REQUESTED",
            requesting_user,
            requesting_ip,
            detail,
        )
        self._schedule_reboot_async(reason)
        return "Server reboot requested", 202

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_once()
            except Exception as exc:
                log.warning("Restart schedule check failed: %s", exc)
            self._stop_event.wait(self._check_interval)

    def check_once(self, *, now=None) -> bool:
        settings = self._store.get_settings()
        schedule = normalise_restart_schedule(
            getattr(settings, "restart_schedule", {}),
            source="server",
            stamp=False,
        )
        tz = timezone_or_utc(settings.timezone)
        now_dt = now.astimezone(tz) if now is not None else datetime.now(tz)
        key = scheduled_minute_key(now_dt, schedule)
        if not key:
            return False
        if key == self._last_runtime_key or self._has_run("server", key):
            return False
        if self._is_blocked():
            log.info("Skipping scheduled server reboot; OTA is busy")
            self._mark_run("server", key)
            return False
        self._mark_run("server", key)
        self._last_runtime_key = key
        self._log_audit(
            "SERVER_REBOOT_REQUESTED",
            "system",
            "",
            f"reason=scheduled key={key}",
        )
        self._schedule_reboot_async("scheduled")
        return True

    def _schedule_reboot_async(self, reason: str) -> None:
        def _worker() -> None:
            time.sleep(self._reboot_delay)
            log.warning("Requesting system reboot (%s)", reason)
            try:
                if privileged.should_use_helper():
                    privileged.request("system.reboot", timeout=15)
                else:
                    subprocess.run(["systemctl", "reboot"], check=False, timeout=15)
            except privileged.PrivilegedHelperError as exc:
                log.error("Privileged reboot request failed: %s", exc)
            except (OSError, subprocess.SubprocessError) as exc:
                log.error("System reboot command failed: %s", exc)

        threading.Thread(target=_worker, name="manual-reboot", daemon=True).start()

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

    def _has_run(self, name: str, key: str) -> bool:
        return self._state().get(name) == key

    def _mark_run(self, name: str, key: str) -> None:
        state = self._state()
        state[name] = key
        self._write_state(state)

    def _log_audit(self, event: str, user: str, ip: str, detail: str) -> None:
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception:
            log.debug("Audit log failed for %s", event)
