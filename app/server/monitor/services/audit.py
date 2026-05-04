# REQ: SWR-009, SWR-057, SWR-068; RISK: RISK-017, RISK-020, RISK-026; SEC: SC-008, SC-020, SC-025; TEST: TC-017, TC-049, TC-055
"""
Security audit logger.

Logs all security-relevant events to /data/logs/audit.log in JSON format.
Thread-safe, append-only. Each line is a standalone JSON object.

Events:
- LOGIN_SUCCESS, LOGIN_FAILED, LOGIN_BLOCKED, LOGIN_RATE_WARN
- LOGIN_PASSWORD_OK_2FA_REQUIRED, LOGIN_2FA_FAILED
- SESSION_EXPIRED, SESSION_LOGOUT
- TOTP_ENROLLED, TOTP_DISABLED, TOTP_VERIFIED, TOTP_RECOVERY_USED
- TOTP_RECOVERY_CODES_REGENERATED, TOTP_RESET_BY_ADMIN
- POLICY_REMOTE_2FA_ENABLED, POLICY_REMOTE_2FA_DISABLED
- CAMERA_PAIRED, CAMERA_REMOVED, CAMERA_OFFLINE, CAMERA_ONLINE
- USER_CREATED, USER_DELETED, PASSWORD_CHANGED
- SETTINGS_CHANGED
- NOTIFICATION_QUIETED
- CLIP_DELETED, RECORDING_ROTATED
- OTA_STARTED, OTA_COMPLETED, OTA_FAILED, OTA_ROLLBACK
- FIREWALL_BLOCKED
- CERT_GENERATED, CERT_REVOKED
- STORAGE_LOW, RETENTION_RISK   (#140 storage health, edge-detected)
- AUDIT_LOG_CLEARED, AUDIT_LOG_EXPORTED, AUDIT_LOG_EXPORT_DENIED
- DIAGNOSTICS_EXPORTED, DIAGNOSTICS_EXPORT_FAILED

Log format (one JSON object per line):
{
    "timestamp": "2026-04-09T14:32:01Z",
    "event": "LOGIN_SUCCESS",
    "user": "admin",
    "ip": "192.168.1.50",
    "detail": "session created"
}

Rotation: max 50MB, retained 90 days.
"""

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("audit")

CLIP_TIMESTAMP_REMUX_OK = "CLIP_TIMESTAMP_REMUX_OK"
CLIP_TIMESTAMP_REMUX_FAILED = "CLIP_TIMESTAMP_REMUX_FAILED"
CLIP_TIMESTAMP_REMUX_DROPPED = "CLIP_TIMESTAMP_REMUX_DROPPED"
CLIP_TIMESTAMP_BACKFILL_STARTED = "CLIP_TIMESTAMP_BACKFILL_STARTED"
CLIP_TIMESTAMP_BACKFILL_COMPLETED = "CLIP_TIMESTAMP_BACKFILL_COMPLETED"
CLIP_TIMESTAMP_BACKFILL_CANCELLED = "CLIP_TIMESTAMP_BACKFILL_CANCELLED"


class AuditLogger:
    """Append-only security event logger."""

    def __init__(self, logs_dir: str):
        self.logs_dir = Path(logs_dir)
        self.log_file = self.logs_dir / "audit.log"
        self._lock = threading.Lock()
        self._listeners: list = []
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def log_event(
        self,
        event: str,
        user: str = "",
        ip: str = "",
        detail: str = "",
    ):
        """Append a security event to the audit log.

        Args:
            event: Event type (e.g., LOGIN_SUCCESS, CAMERA_PAIRED)
            user: Username associated with the event
            ip: IP address associated with the event
            detail: Additional detail string
        """
        entry = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": event,
            "user": user,
            "ip": ip,
            "detail": detail,
        }
        line = json.dumps(entry, separators=(",", ":"))

        with self._lock:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                log.error("Failed to write audit log: %s", e)

        log.info("AUDIT: %s user=%s ip=%s detail=%s", event, user, ip, detail)
        self._notify_listeners(entry)

    def clear_events(self, user: str = "", ip: str = "") -> None:
        """Replace the audit log with an AUDIT_LOG_CLEARED sentinel.

        Atomic under _lock: no concurrent log_event can interleave between
        replacement and the sentinel write, preserving chain of custody.
        The cleared log always begins with a record of who cleared it.

        The sentinel is written to a temporary file and atomically replaced
        into place so in-flight readers keep streaming the pre-clear snapshot
        from their existing file descriptor.
        """
        entry = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "AUDIT_LOG_CLEARED",
            "user": user,
            "ip": ip,
            "detail": "audit log cleared by admin",
        }
        line = json.dumps(entry, separators=(",", ":"))

        with self._lock:
            try:
                temp_file = self.log_file.with_suffix(".log.tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    f.write(line + "\n")
                os.replace(temp_file, self.log_file)
            except OSError as e:
                log.error("Failed to clear audit log: %s", e)
                return

        log.info("AUDIT: AUDIT_LOG_CLEARED user=%s ip=%s", user, ip)
        self._notify_listeners(entry)

    def get_events(self, limit: int = 100, event_type: str = "") -> list[dict]:
        """Read recent events from the audit log.

        Args:
            limit: Maximum number of events to return (most recent first)
            event_type: Filter by event type (empty = all)

        Returns:
            List of event dicts, most recent first.
        """
        if not self.log_file.exists():
            return []

        try:
            lines = self.log_file.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return []

        events = []
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event_type and entry.get("event") != event_type:
                continue
            events.append(entry)
            if len(events) >= limit:
                break
        return events

    def iter_events(
        self,
        start: str = "",
        end: str = "",
        event_type: str = "",
        actor: str = "",
    ):
        """Yield audit entries oldest-first without buffering the whole file.

        Args:
            start: Inclusive lower timestamp bound (`YYYY-mm-ddTHH:MM:SSZ`).
            end: Inclusive upper timestamp bound (`YYYY-mm-ddTHH:MM:SSZ`).
            event_type: Optional exact event name or comma-separated names.
            actor: Optional exact user/actor match.
        """
        if not self.log_file.exists():
            return iter(())

        allowed_events = {
            value.strip() for value in event_type.split(",") if value.strip()
        }

        try:
            handle = open(self.log_file, encoding="utf-8")
        except OSError:
            return iter(())

        handle.seek(0, os.SEEK_END)
        snapshot_end = handle.tell()
        handle.seek(0)

        def _iter():
            try:
                while handle.tell() < snapshot_end:
                    line = handle.readline()
                    if not line:
                        break
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    timestamp = entry.get("timestamp", "")
                    if start and timestamp < start:
                        continue
                    if end and timestamp > end:
                        continue
                    if allowed_events and entry.get("event") not in allowed_events:
                        continue
                    if actor and entry.get("user", "") != actor:
                        continue
                    yield entry
            finally:
                handle.close()

        return _iter()

    def add_listener(self, listener) -> None:
        """Register a best-effort callback for newly written audit entries."""
        if not callable(listener):
            return
        with self._lock:
            self._listeners.append(listener)

    def _notify_listeners(self, entry: dict) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(dict(entry))
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("Audit listener failed: %s", exc)
