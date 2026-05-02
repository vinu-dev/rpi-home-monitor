# REQ: SWR-017, SWR-033; RISK: RISK-005, RISK-016, RISK-020; SEC: SC-008, SC-015, SC-020; TEST: TC-014, TC-031
"""
AlertCenterService — derive-on-read view over existing event sources.

Implements ADR-0024. The alert center is **not** a new persistent store
of alerts — alerts are derived on every read from the three existing
event sources:

  AuditLogger          (security-relevant audit events)
  MotionEventStore     (motion detections with clip correlation)
  Camera.hardware_faults (per-heartbeat fault snapshot, ADR-0023)

The only new persistent state is a small per-user read-flag map at
``/data/config/alert_read_state.json``. Atomic writes (tempfile +
os.replace) match the rest of the codebase (ADR-0002).

Catalogue (per ADR-0024 §"What counts as an alert"):

  Faults:  severity in {warning, error, critical}  (info excluded)
  Audit:   OTA_FAILED, OTA_ROLLBACK, CAMERA_OFFLINE,
           CERT_REVOKED, FIREWALL_BLOCKED
  Motion:  closed events with peak_score >= MOTION_NOTIFICATION_THRESHOLD
           (default; per-camera override is a future-slice concern)

Permission model (defence-in-depth — server-side filter is the source of
truth, the UI does not render stale-but-ungated rows):

  Viewers see fault- and motion-derived alerts only.
  Admins see everything (faults + audit + motion).

Alert IDs are stable across reads so per-user state survives. Typed
prefixes:

  fault:<camera_id>:<code>           — derived from Camera.hardware_faults
  audit:<sha256(line)[:16]>          — content-hashed audit line
  motion:<motion_event_id>           — natural ID from MotionEventStore
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("monitor.alert_center")


# ---------------------------------------------------------------------------
# Catalogue — what counts as an alert
# ---------------------------------------------------------------------------

# Audit event names that are user-visible alerts. Per ADR-0024:
#   LOGIN_FAILED is intentionally excluded — the audit teaser
#   (issue #148) is the right surface for login storms; surfacing
#   them in the alert center too would drown the inbox.
ALERT_AUDIT_EVENTS: frozenset[str] = frozenset(
    {
        "OTA_FAILED",
        "OTA_ROLLBACK",
        "CAMERA_OFFLINE",
        "CERT_REVOKED",
        "FIREWALL_BLOCKED",
        # #140 — storage health alerts emitted by LoopRecorder.tick()
        # on threshold-crossing edges (not every metrics tick). Distinct
        # severities so the inbox surfaces them differently.
        "STORAGE_LOW",
        "RETENTION_RISK",
    }
)

# Severity threshold for fault-derived alerts. info-level faults are
# diagnostic (e.g. "thermal headroom shrinking") and stay on the
# camera card, not in the inbox.
ALERT_FAULT_SEVERITIES: frozenset[str] = frozenset({"warning", "error", "critical"})

# Per-camera notification threshold default. Each motion event with a
# peak_score at or above this is alert-worthy. ADR-0024 defers a
# per-camera tunable to a later slice (#121); for now, one global
# default that's well above ambient sensor noise.
MOTION_NOTIFICATION_THRESHOLD: float = 0.05

# Default severity mapping for audit events. The audit log has no
# native severity field, so the alert center assigns one from the
# catalogue. Anything not listed gets "warning" as a safe default.
_AUDIT_SEVERITY: dict[str, str] = {
    "OTA_FAILED": "error",
    "OTA_ROLLBACK": "warning",
    "CAMERA_OFFLINE": "warning",
    "CERT_REVOKED": "critical",
    "FIREWALL_BLOCKED": "warning",
    # #140
    "STORAGE_LOW": "warning",  # heads-up before auto-FIFO cleanup
    "RETENTION_RISK": "error",  # FIFO is actively running; user
    # retention is being violated
}

# Severity ordering so the API can sort / filter by "at least this bad."
_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}

# Schema version for the read-state file. Bump on any breaking shape
# change so a forward-rolling install can migrate cleanly. v1 is the
# initial layout below.
READ_STATE_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Alert dataclass — wire-format shape
# ---------------------------------------------------------------------------


@dataclass
class Alert:
    """Single row in the alert center.

    Derived on read; not persisted as such. ``is_read`` and ``read_at``
    are joined from the per-user read-state file.
    """

    id: str
    source: str  # "fault" | "audit" | "motion"
    severity: str  # "info" | "warning" | "error" | "critical"
    timestamp: str  # ISO-8601 UTC with trailing Z
    subject: dict  # {"type": "camera", "id": "cam-d8ee"} | {"type": "server"}
    message: str
    deep_link: str
    is_read: bool = False
    read_at: str | None = None
    hint: str = ""
    context: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AlertCenterService:
    """Derive alerts on read from existing event sources; persist read state.

    Constructor is dependency-injected (service-layer pattern, ADR-0003).
    No I/O in ``__init__`` beyond a one-time read-state file read; all
    real work happens in the public methods.

    Args:
        store: Server-side data store (used to enumerate cameras +
            their persisted hardware_faults).
        audit_logger: AuditLogger for the audit-derived alerts.
        motion_event_store: MotionEventStore for motion-derived alerts.
        read_state_path: Where to persist the per-user read-flag JSON.
            Created on first write; safe to point at a path that
            doesn't exist yet.
    """

    def __init__(
        self,
        *,
        store,
        audit_logger,
        motion_event_store,
        read_state_path: str | Path,
    ):
        self._store = store
        self._audit = audit_logger
        self._motion = motion_event_store
        self._path = Path(read_state_path)
        self._lock = threading.Lock()
        self._read_state: dict[str, dict[str, str]] = self._load_read_state()

    # -- public API ---------------------------------------------------------

    def list_alerts(
        self,
        *,
        user: str,
        role: str,
        source: str | None = None,
        severity: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
        before: str | None = None,
        sort: str = "timestamp",
    ) -> list[dict]:
        """Return alerts visible to ``(user, role)``, ordered per ``sort``.

        Filters and the role-aware permission gate are applied
        server-side. The dashboard / inbox UI may not bypass these
        by sending a different role string — Flask's session/role
        machinery (``admin_required`` / ``login_required``) is the
        binding source of truth at the API layer.

        Sort modes (#144 review queue):
          ``"timestamp"`` — newest first (default, the inbox view).
          ``"importance"`` — severity DESC, then timestamp DESC.
                             This is the *review queue* ordering: the
                             operator scans the most important
                             unread items first per the
                             ``r1-review-queue.md`` spec.
        """
        alerts = self._compute_alerts(role=role)
        alerts = self._apply_read_state(alerts, user=user)

        if source:
            alerts = [a for a in alerts if a.source == source]
        if severity:
            min_rank = _SEVERITY_ORDER.get(severity, -1)
            alerts = [
                a for a in alerts if _SEVERITY_ORDER.get(a.severity, 0) >= min_rank
            ]
        if unread_only:
            alerts = [a for a in alerts if not a.is_read]
        if before:
            alerts = [a for a in alerts if a.timestamp < before]

        if sort == "importance":
            # Sort by severity rank DESC, then timestamp DESC. A stable
            # sort means same-rank alerts keep their relative
            # newest-first order.
            alerts.sort(key=lambda a: a.timestamp, reverse=True)
            alerts.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 0), reverse=True)
        else:
            # Default: newest first. Stable sort preserves source-
            # derived insertion order on same-second alerts.
            alerts.sort(key=lambda a: a.timestamp, reverse=True)

        if limit > 0:
            alerts = alerts[:limit]

        return [asdict(a) for a in alerts]

    def unread_count(self, *, user: str, role: str) -> int:
        """Cheap count for the nav badge.

        Walks the same compute path as ``list_alerts`` but skips the
        sort + serialise. The cost is bounded by audit_log_lines +
        motion_events + active_faults — all small.
        """
        alerts = self._compute_alerts(role=role)
        alerts = self._apply_read_state(alerts, user=user)
        return sum(1 for a in alerts if not a.is_read)

    def mark_read(self, *, user: str, alert_id: str) -> bool:
        """Mark a single alert as read for this user. Idempotent.

        Returns True on success, False on a clearly bogus alert_id
        shape (the only failure mode — we do NOT validate that the
        alert currently exists, because the source might be paginated
        or compacted between the user's view and the click).
        """
        if not _looks_like_alert_id(alert_id):
            return False
        if not user:
            return False

        ts = _now_z()
        with self._lock:
            self._read_state.setdefault(user, {})[alert_id] = ts
            self._persist_locked()
        return True

    def mark_all_read(
        self,
        *,
        user: str,
        role: str,
        source: str | None = None,
        severity: str | None = None,
        before: str | None = None,
    ) -> int:
        """Mark every alert matching the same filters as ``list_alerts``.

        Filters mirror ``list_alerts`` so a user clicking "mark all
        read" while filtered to severity=error doesn't accidentally
        clear the warning badge too. Returns the count actually
        flipped (i.e. previously unread + now-read). Re-marking
        already-read alerts does not increment the count.
        """
        alerts = self._compute_alerts(role=role)
        alerts = self._apply_read_state(alerts, user=user)

        if source:
            alerts = [a for a in alerts if a.source == source]
        if severity:
            min_rank = _SEVERITY_ORDER.get(severity, -1)
            alerts = [
                a for a in alerts if _SEVERITY_ORDER.get(a.severity, 0) >= min_rank
            ]
        if before:
            alerts = [a for a in alerts if a.timestamp < before]

        ts = _now_z()
        flipped = 0
        with self._lock:
            user_state = self._read_state.setdefault(user, {})
            for a in alerts:
                if a.is_read:
                    continue
                user_state[a.id] = ts
                flipped += 1
            if flipped:
                self._persist_locked()
        return flipped

    def forget_user(self, user: str) -> bool:
        """Drop all read state for a deleted user.

        Called by UserService.delete via the cascade hook documented
        in ADR-0024 §"Storage". Idempotent — calling on an unknown
        user is a no-op.
        """
        if not user:
            return False
        with self._lock:
            if user not in self._read_state:
                return False
            del self._read_state[user]
            self._persist_locked()
        return True

    # -- alert derivation ---------------------------------------------------

    def _compute_alerts(self, *, role: str) -> list[Alert]:
        """Walk all three sources and emit alert records.

        Permission gate applied here, not at the API layer, so any
        future caller (e.g. the dashboard summary service drilling
        into a "you have N alerts" deep-link) gets the same view.
        """
        out: list[Alert] = []
        is_admin = role == "admin"

        # --- faults (visible to everyone) ---
        try:
            cameras = self._store.get_cameras()
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("alert_center: failed to load cameras: %s", exc)
            cameras = []

        for cam in cameras:
            for fault in getattr(cam, "hardware_faults", None) or []:
                if not isinstance(fault, dict):
                    continue
                severity = fault.get("severity") or "warning"
                if severity not in ALERT_FAULT_SEVERITIES:
                    continue
                code = fault.get("code") or "unknown"
                ts = fault.get("opened_at") or fault.get("timestamp") or _now_z()
                out.append(
                    Alert(
                        id=f"fault:{cam.id}:{code}",
                        source="fault",
                        severity=severity,
                        timestamp=_normalise_iso_z(ts),
                        subject={"type": "camera", "id": cam.id},
                        message=fault.get("message") or code,
                        hint=fault.get("hint") or "",
                        context=fault.get("context") or {},
                        deep_link="/dashboard#cameras-section",
                    )
                )

        # --- audit (admin only) ---
        if is_admin:
            try:
                audit_events = self._audit.get_events(limit=500)
            except Exception as exc:  # pragma: no cover
                log.warning("alert_center: failed to load audit: %s", exc)
                audit_events = []

            for ev in audit_events:
                code = ev.get("event") or ""
                if code not in ALERT_AUDIT_EVENTS:
                    continue
                line_repr = json.dumps(ev, sort_keys=True, separators=(",", ":"))
                alert_id = (
                    f"audit:{hashlib.sha256(line_repr.encode()).hexdigest()[:16]}"
                )
                out.append(
                    Alert(
                        id=alert_id,
                        source="audit",
                        severity=_AUDIT_SEVERITY.get(code, "warning"),
                        timestamp=_normalise_iso_z(ev.get("timestamp") or _now_z()),
                        subject=_audit_subject(ev),
                        message=_audit_message(ev),
                        context={
                            "user": ev.get("user", ""),
                            "ip": ev.get("ip", ""),
                            "detail": ev.get("detail", ""),
                        },
                        deep_link="/logs",
                    )
                )

        # --- motion (visible to everyone) ---
        try:
            motion_events = self._motion.list_events(limit=500)
        except Exception as exc:  # pragma: no cover
            log.warning("alert_center: failed to load motion events: %s", exc)
            motion_events = []

        for evt in motion_events:
            ended_at = getattr(evt, "ended_at", None)
            if not ended_at:
                continue  # still in progress; not yet alert-worthy
            peak_score = float(getattr(evt, "peak_score", 0.0) or 0.0)
            if peak_score < MOTION_NOTIFICATION_THRESHOLD:
                continue
            cam_id = getattr(evt, "camera_id", "") or ""
            evt_id = getattr(evt, "id", "") or ""
            started_at = getattr(evt, "started_at", "") or _now_z()
            out.append(
                Alert(
                    id=f"motion:{evt_id}",
                    source="motion",
                    severity="info" if peak_score < 0.10 else "warning",
                    timestamp=_normalise_iso_z(started_at),
                    subject={"type": "camera", "id": cam_id},
                    message=f"Motion detected on {cam_id}",
                    context={
                        "peak_score": peak_score,
                        "duration_seconds": getattr(evt, "duration_seconds", 0),
                    },
                    deep_link=f"/events/{evt_id}",
                )
            )

        return out

    # -- read state ---------------------------------------------------------

    def _apply_read_state(self, alerts: list[Alert], *, user: str) -> list[Alert]:
        """Annotate alerts with is_read / read_at for ``user``.

        Read state never travels across users — each user has their
        own map. ADR-0024 §Q2.
        """
        with self._lock:
            user_state = dict(self._read_state.get(user, {}))
        for a in alerts:
            ts = user_state.get(a.id)
            if ts:
                a.is_read = True
                a.read_at = ts
        return alerts

    def _load_read_state(self) -> dict[str, dict[str, str]]:
        """Read the per-user read-state file. Tolerant of missing/corrupt."""
        if not self._path.exists():
            return {}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "alert_center: read-state file unreadable, starting empty: %s", exc
            )
            return {}

        if not isinstance(data, dict):
            return {}
        users = data.get("users", {})
        if not isinstance(users, dict):
            return {}

        # Defensive shape check — drop anything that doesn't match.
        clean: dict[str, dict[str, str]] = {}
        for u, alerts in users.items():
            if not isinstance(u, str) or not isinstance(alerts, dict):
                continue
            clean[u] = {
                k: v
                for k, v in alerts.items()
                if isinstance(k, str) and isinstance(v, str)
            }
        return clean

    def _persist_locked(self) -> None:
        """Atomic write of the read-state file. Caller holds ``self._lock``."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": READ_STATE_SCHEMA_VERSION,
            "users": self._read_state,
        }
        # Atomic replace via tempfile in the same directory (must be
        # same fs for os.replace to be atomic).
        fd, tmp_path = tempfile.mkstemp(
            prefix="alert_read_state.", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
            os.replace(tmp_path, self._path)
        except OSError as exc:
            log.error("alert_center: failed to persist read state: %s", exc)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_iso_z(value: str) -> str:
    """Coerce an ISO-8601 timestamp into ``YYYY-MM-DDTHH:MM:SSZ``.

    The audit log uses Z-suffixed UTC, motion events use isoformat with
    a +00:00 offset, and some heartbeat-derived faults round-trip via
    Python's datetime so they may also use offset notation. Sort
    correctness depends on a single representation, so normalise.
    """
    if not isinstance(value, str) or not value:
        return _now_z()
    try:
        # datetime.fromisoformat handles both Z and offset notations
        # in Python 3.11+ (the runtime target per pyproject.toml).
        normalised = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised).astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return value  # fall back to raw — sort still terminates


def _looks_like_alert_id(alert_id: str) -> bool:
    """Cheap shape-check so the API doesn't blindly persist garbage.

    Real validation lives at the source — but reading or storing an
    alert_id with no recognised prefix is always a programming error,
    not user input we should accept silently.
    """
    if not isinstance(alert_id, str) or not alert_id:
        return False
    return any(
        alert_id.startswith(prefix) for prefix in ("fault:", "audit:", "motion:")
    )


def _audit_subject(ev: dict) -> dict:
    """Best-effort subject inference for an audit-derived alert.

    Some audit codes carry a camera_id in ``detail`` (e.g. CAMERA_OFFLINE
    "cam-d8ee gone offline"); when we can't be sure, anchor it on the
    server.
    """
    code = ev.get("event") or ""
    detail = ev.get("detail") or ""
    if code in {"CAMERA_OFFLINE"} and detail:
        # Heuristic: first whitespace-delimited token.
        token = detail.split()[0] if detail else ""
        if token.startswith("cam-") or token.startswith("camera-"):
            return {"type": "camera", "id": token.rstrip(":,;")}
    return {"type": "server"}


def _audit_message(ev: dict) -> str:
    """Human-friendly one-line message for an audit-derived alert."""
    code = ev.get("event") or "EVENT"
    detail = (ev.get("detail") or "").strip()
    label_map = {
        "OTA_FAILED": "OTA update failed",
        "OTA_ROLLBACK": "OTA update rolled back",
        "CAMERA_OFFLINE": "Camera went offline",
        "CERT_REVOKED": "Certificate revoked",
        "FIREWALL_BLOCKED": "Firewall blocked traffic",
        # #140
        "STORAGE_LOW": "Recordings storage low",
        "RETENTION_RISK": "Retention at risk — auto-deleting recordings",
    }
    label = label_map.get(code, code.replace("_", " ").lower().capitalize())
    if detail:
        return f"{label}: {detail}"
    return label
