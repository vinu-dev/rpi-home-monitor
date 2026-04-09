"""
Security audit logger.

Logs all security-relevant events to /data/logs/audit.log in JSON format.
Thread-safe, append-only. Each line is a standalone JSON object.

Events:
- LOGIN_SUCCESS, LOGIN_FAILED
- SESSION_EXPIRED, SESSION_LOGOUT
- CAMERA_PAIRED, CAMERA_REMOVED, CAMERA_OFFLINE, CAMERA_ONLINE
- USER_CREATED, USER_DELETED, PASSWORD_CHANGED
- SETTINGS_CHANGED
- CLIP_DELETED
- OTA_STARTED, OTA_COMPLETED, OTA_FAILED, OTA_ROLLBACK
- FIREWALL_BLOCKED
- CERT_GENERATED, CERT_REVOKED

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
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("audit")


class AuditLogger:
    """Append-only security event logger."""

    def __init__(self, logs_dir: str):
        self.logs_dir = Path(logs_dir)
        self.log_file = self.logs_dir / "audit.log"
        self._lock = threading.Lock()
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
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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
