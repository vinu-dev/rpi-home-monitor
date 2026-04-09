"""
Security audit logger.

Logs all security-relevant events to /data/logs/audit.log in JSON format.

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

Log format:
{
    "timestamp": "2026-04-09T14:32:01Z",
    "event": "LOGIN_SUCCESS",
    "user": "admin",
    "ip": "192.168.1.50",
    "detail": "session created"
}

Rotation: max 50MB, retained 90 days.
"""

# TODO: Implement AuditLogger class
