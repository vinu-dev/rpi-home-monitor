# REQ: SWR-033, SWR-041; RISK: RISK-016; SEC: SC-015; TEST: TC-031
"""
NotificationPolicyService - derive browser-notification eligibility for
motion events and throttle-transition audits.

Implements ADR-0027 (#121, #128) and the quiet-hours follow-up in #245.
The alert center remains the persistent triage surface (ADR-0024); this
service decides which of those motion alerts ALSO warrant an OS-level
browser notification, applies per-camera filtering, quiet-hours
suppression, and coalescing, and tracks which notifications a user has
already acknowledged via the polling client.

Decision tree per motion event (called from polling fetch):

  1. event.peak_score < MOTION_NOTIFICATION_THRESHOLD
     -> already filtered by AlertCenterService; not surfaced here either.
  2. event.duration_seconds < camera.notification_rule.min_duration_seconds
     -> drop. Sub-3s default is the noise floor.
  3. user.notification_prefs.enabled is False
     -> handled at the select_for_user gate; return no notifications.
  4. camera.notification_rule.enabled is False (with per-user override)
     -> drop (per-camera mute).
  5. event.ended_at falls inside quiet hours
     -> suppress, emit one rate-limited NOTIFICATION_QUIETED audit entry,
        do not stamp last_notification_at.
  6. now - camera.last_notification_at < camera coalesce window
     -> suppress (event still in inbox; just no OS-level surface).
  7. event.started_at <= user.last_notification_seen_at
     -> handled by the since cursor before _eligible runs.
  8. Otherwise -> eligible. Stamp camera.last_notification_at = now.

Concurrency: persisted delivery state still lives on Camera + User
records via Store. The service keeps one small in-memory cooldown map
only for NOTIFICATION_QUIETED audit rate-limiting.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from monitor.services.alert_center_service import MOTION_NOTIFICATION_THRESHOLD
from monitor.services.notification_schedule import (
    evaluate_quiet_hours,
    validate_schedule,
)

log = logging.getLogger("monitor.notification_policy")

THROTTLE_AUDIT_EVENT = "CAMERA_THROTTLED"
_THROTTLE_AUDIT_RE = re.compile(
    r"^camera (?P<camera_id>cam-[a-z0-9]{1,48}) sticky throttle bits set: "
    r"(?P<labels>.+)$"
)

# Hard caps on the per-camera tunables. Enforced at PUT time so the
# UI can't store nonsense that breaks the decision tree. Documented
# in ADR-0027's resolved open questions.
MIN_DURATION_LO = 1
MIN_DURATION_HI = 60
COALESCE_LO = 10
COALESCE_HI = 600

_QUIET_AUDIT_TTL = timedelta(days=2)


class NotificationPolicyService:
    """Derive notification eligibility for motion events and throttle audits."""

    def __init__(self, *, store, motion_event_store, audit=None, audit_logger=None):
        self._store = store
        self._motion = motion_event_store
        self._audit = audit if audit is not None else audit_logger
        self._quiet_audit_lock = Lock()
        self._quiet_audit_windows: dict[tuple[str, str, str, str], datetime] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_for_user(
        self, *, user: str, since: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Return surfaceable notifications for ``user`` newer
        than ``since`` (ISO-8601 Z), capped at ``limit``.

        Each entry is the wire shape consumed by
        ``GET /api/v1/notifications/pending``.
        """
        user_obj = self._get_user(user)
        if user_obj is None:
            return []
        prefs = self._stored_prefs(user_obj)
        if not prefs.get("enabled", False):
            return []

        since_iso = since or getattr(user_obj, "last_notification_seen_at", "") or ""

        cameras_by_id = {}
        try:
            for camera in self._store.get_cameras():
                cameras_by_id[camera.id] = camera
        except Exception as exc:  # pragma: no cover
            log.warning("notification_policy: cameras fetch failed: %s", exc)
            return []

        throttle_out: list[dict] = []
        motion_out: list[dict] = []

        if self._audit is not None:
            try:
                audit_events = self._audit.get_events(
                    limit=200,
                    event_type=THROTTLE_AUDIT_EVENT,
                )
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("notification_policy: audit fetch failed: %s", exc)
                audit_events = []

            for ev in audit_events:
                note = self._wire_throttle(ev, cameras_by_id, prefs, user_obj)
                if note is None:
                    continue
                if since_iso and note.get("started_at", "") <= since_iso:
                    continue
                throttle_out.append(note)

        throttle_out.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        remaining = max(0, limit - len(throttle_out))

        if remaining > 0:
            try:
                events = self._motion.list_events(limit=200)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("notification_policy: motion fetch failed: %s", exc)
                events = []

            for evt in events:
                if not getattr(evt, "ended_at", None):
                    continue
                if since_iso and getattr(evt, "started_at", "") <= since_iso:
                    continue
                cam = cameras_by_id.get(getattr(evt, "camera_id", ""))
                if cam is None:
                    continue
                if not self._eligible(evt, cam, user_obj, prefs):
                    continue

                motion_out.append(self._wire(evt, cam))
                if len(motion_out) >= remaining:
                    break

        out = throttle_out + motion_out
        out.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        return out[:limit]

    def mark_seen(self, *, user: str, alert_ids: list[str]) -> int:
        """Advance ``user.last_notification_seen_at`` to the newest
        delivered alert, persist. Returns the count actually marked
        (i.e. those whose id maps to a known notification source).
        """
        user_obj = self._get_user(user)
        if user_obj is None or not alert_ids:
            return 0

        latest = ""
        marked = 0
        throttle_timestamps: dict[str, str] = {}
        if self._audit is not None and any(
            aid.startswith("throttle:") for aid in alert_ids
        ):
            try:
                audit_events = self._audit.get_events(
                    limit=500,
                    event_type=THROTTLE_AUDIT_EVENT,
                )
            except Exception:  # pragma: no cover - defensive
                audit_events = []
            for ev in audit_events:
                throttle_timestamps[self._audit_alert_id(ev)] = (
                    ev.get("timestamp") or ""
                )

        for aid in alert_ids:
            ts = ""
            if aid.startswith("motion:") or not aid.startswith("throttle:"):
                evt_id = (
                    aid.removeprefix("motion:") if aid.startswith("motion:") else aid
                )
                try:
                    evt = self._motion.get(evt_id)
                except Exception:
                    evt = None
                if evt is not None:
                    ts = getattr(evt, "started_at", "")
            else:
                ts = throttle_timestamps.get(aid, "")
            if not ts:
                continue
            if ts and ts > latest:
                latest = ts
            marked += 1

        if latest and latest > (
            getattr(user_obj, "last_notification_seen_at", "") or ""
        ):
            user_obj.last_notification_seen_at = latest
            try:
                self._store.save_user(user_obj)
            except Exception as exc:  # pragma: no cover
                log.warning("notification_policy: save_user failed: %s", exc)
        return marked

    def get_prefs(self, user: str) -> dict:
        """Return the user's current notification preferences."""
        user_obj = self._get_user(user)
        if user_obj is None:
            return {"enabled": False, "cameras": {}, "notification_schedule": []}
        return self._prefs_response(user_obj)

    def update_prefs(
        self, *, user: str, payload: dict, ip: str = ""
    ) -> tuple[dict, str]:
        """Validate + persist a partial-update of the user's prefs."""
        user_obj = self._get_user(user)
        if user_obj is None:
            return {}, "user not found"

        prefs = dict(self._stored_prefs(user_obj))
        notification_schedule = list(
            getattr(user_obj, "notification_schedule", None) or []
        )
        audit_detail_fields: set[str] = set()

        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                return {}, "enabled must be a boolean"
            prefs["enabled"] = payload["enabled"]

        if "notification_schedule" in payload:
            err = validate_schedule(payload["notification_schedule"])
            if err:
                return {}, err
            notification_schedule = list(payload["notification_schedule"])
            audit_detail_fields.add("notification_schedule")

        if "cameras" in payload:
            cams = payload["cameras"]
            if not isinstance(cams, dict):
                return {}, "cameras must be an object"
            new_cameras: dict[str, dict] = {}
            for cam_id, override in cams.items():
                if not isinstance(cam_id, str):
                    return {}, "cameras keys must be strings"
                if override is None:
                    continue
                if not isinstance(override, dict):
                    return {}, f"cameras[{cam_id}] must be an object or null"

                clean: dict[str, Any] = {}
                if "enabled" in override:
                    if override["enabled"] is None:
                        pass
                    elif not isinstance(override["enabled"], bool):
                        return (
                            {},
                            f"cameras[{cam_id}].enabled must be a boolean or null",
                        )
                    else:
                        clean["enabled"] = override["enabled"]
                if "min_duration_seconds" in override:
                    err = self._validate_int(
                        override["min_duration_seconds"],
                        MIN_DURATION_LO,
                        MIN_DURATION_HI,
                        f"cameras[{cam_id}].min_duration_seconds",
                    )
                    if err:
                        return {}, err
                    if override["min_duration_seconds"] is not None:
                        clean["min_duration_seconds"] = override["min_duration_seconds"]
                if "coalesce_seconds" in override:
                    err = self._validate_int(
                        override["coalesce_seconds"],
                        COALESCE_LO,
                        COALESCE_HI,
                        f"cameras[{cam_id}].coalesce_seconds",
                    )
                    if err:
                        return {}, err
                    if override["coalesce_seconds"] is not None:
                        clean["coalesce_seconds"] = override["coalesce_seconds"]
                if "quiet_schedule" in override:
                    if override["quiet_schedule"] is None:
                        pass
                    else:
                        err = validate_schedule(
                            override["quiet_schedule"],
                            label=f"cameras[{cam_id}].quiet_schedule",
                        )
                        if err:
                            return {}, err
                        clean["quiet_schedule"] = list(override["quiet_schedule"])
                    audit_detail_fields.add(f"quiet_schedule:{cam_id}")
                if clean:
                    new_cameras[cam_id] = clean
            prefs["cameras"] = new_cameras

        user_obj.notification_prefs = prefs
        user_obj.notification_schedule = notification_schedule
        try:
            self._store.save_user(user_obj)
        except Exception as exc:  # pragma: no cover
            log.warning("notification_policy: save_user failed: %s", exc)
            return {}, "internal error"

        if audit_detail_fields:
            self._log_audit(
                event="SETTINGS_CHANGED",
                user=user,
                ip=ip,
                detail=f"updated: {', '.join(sorted(audit_detail_fields))}",
            )

        return self._prefs_response(user_obj), ""

    # ------------------------------------------------------------------
    # Decision tree internals
    # ------------------------------------------------------------------

    def _eligible(self, evt, cam, user_obj, prefs: dict) -> bool:
        """Apply the motion notification filter chain."""
        peak_score = float(getattr(evt, "peak_score", 0.0) or 0.0)
        if peak_score < MOTION_NOTIFICATION_THRESHOLD:
            return False

        rule = self._effective_rule(cam, prefs)
        if not rule.get("enabled", True):
            return False

        duration = float(getattr(evt, "duration_seconds", 0.0) or 0.0)
        if duration < rule.get("min_duration_seconds", 3):
            return False

        now = datetime.now(UTC)
        override = (prefs.get("cameras") or {}).get(getattr(cam, "id", ""))
        quiet_override = (
            override.get("quiet_schedule")
            if isinstance(override, dict) and "quiet_schedule" in override
            else None
        )
        decision = evaluate_quiet_hours(
            now=self._event_timestamp(evt) or now,
            user_schedule=getattr(user_obj, "notification_schedule", None) or [],
            camera_override=quiet_override,
            tz=self._current_timezone(),
        )
        if decision.quiet:
            self._emit_quiet_audit(
                user=user_obj,
                cam=cam,
                evt=evt,
                now=now,
                window_key=decision.window_key,
                source=decision.source,
            )
            return False

        last_at = getattr(cam, "last_notification_at", "") or ""
        if last_at:
            try:
                last_dt = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
                elapsed = (now - last_dt).total_seconds()
                if elapsed < rule.get("coalesce_seconds", 60):
                    return False
            except (ValueError, TypeError):
                pass

        cam.last_notification_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            self._store.save_camera(cam)
        except Exception as exc:  # pragma: no cover
            log.warning("notification_policy: save_camera failed: %s", exc)
        return True

    def _effective_rule(self, cam, prefs: dict) -> dict:
        """Compose the camera default with the per-user override."""
        camera_rule = getattr(cam, "notification_rule", None) or {
            "enabled": True,
            "min_duration_seconds": 3,
            "coalesce_seconds": 60,
        }
        result = dict(camera_rule)
        override = (prefs.get("cameras") or {}).get(getattr(cam, "id", ""))
        if isinstance(override, dict):
            for key in ("enabled", "min_duration_seconds", "coalesce_seconds"):
                if key in override and override[key] is not None:
                    result[key] = override[key]
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_user(self, username: str):
        """Best-effort user lookup by username."""
        try:
            users = list(self._store.get_users() or [])
        except Exception:
            return None
        for user in users:
            if getattr(user, "username", None) == username:
                return user
        return None

    @staticmethod
    def _stored_prefs(user_obj) -> dict:
        """Return persisted prefs with safe legacy defaults."""
        prefs = getattr(user_obj, "notification_prefs", None) or {}
        return {
            "enabled": bool(prefs.get("enabled", False)),
            "cameras": dict(prefs.get("cameras", {}) or {}),
        }

    def _prefs_response(self, user_obj) -> dict:
        """Shape the prefs payload returned by the API."""
        prefs = self._stored_prefs(user_obj)
        prefs["notification_schedule"] = list(
            getattr(user_obj, "notification_schedule", None) or []
        )
        return prefs

    @staticmethod
    def _validate_int(value: Any, lo: int, hi: int, label: str) -> str:
        """Validate an int range; allow None for "use default"."""
        if value is None:
            return ""
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{label} must be an integer"
        if value < lo or value > hi:
            return f"{label} must be {lo}..{hi}"
        return ""

    @staticmethod
    def _event_timestamp(evt) -> datetime | None:
        value = getattr(evt, "ended_at", None) or getattr(evt, "started_at", None) or ""
        return NotificationPolicyService._parse_timestamp(value)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
                UTC
            )
        except (ValueError, TypeError):
            return None

    def _current_timezone(self) -> str:
        try:
            settings = self._store.get_settings()
        except Exception:  # pragma: no cover - defensive
            return "UTC"
        return getattr(settings, "timezone", "") or "UTC"

    def _emit_quiet_audit(
        self, *, user, cam, evt, now: datetime, window_key: str, source: str
    ) -> None:
        self._emit_quiet_audit_ref(
            user=user,
            camera_id=getattr(cam, "id", "") or "",
            event_class="motion",
            reference=f"motion_event_id={getattr(evt, 'id', '')}",
            now=now,
            window_key=window_key,
            source=source,
        )

    def _emit_quiet_audit_ref(
        self,
        *,
        user,
        camera_id: str,
        event_class: str,
        reference: str,
        now: datetime,
        window_key: str,
        source: str,
    ) -> None:
        if not self._audit or not window_key:
            return

        username = getattr(user, "username", "") or ""
        key = (username, camera_id, event_class, window_key)

        with self._quiet_audit_lock:
            cutoff = now - _QUIET_AUDIT_TTL
            self._quiet_audit_windows = {
                item_key: ts
                for item_key, ts in self._quiet_audit_windows.items()
                if ts >= cutoff
            }
            if key in self._quiet_audit_windows:
                return
            self._quiet_audit_windows[key] = now

        detail = (
            f"camera_id={camera_id} class={event_class} {reference} source={source}"
        )
        self._log_audit(
            event="NOTIFICATION_QUIETED",
            user=username,
            ip="",
            detail=detail,
        )

    def _log_audit(self, *, event: str, user: str, ip: str, detail: str) -> None:
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("notification_policy: audit failed: %s", exc)

    def _wire(self, evt, cam) -> dict:
        """Shape a motion event + its correlated clip into the wire format."""
        evt_dict = asdict(evt) if hasattr(evt, "__dataclass_fields__") else dict(evt)
        clip_ref = evt_dict.get("clip_ref") or None
        snapshot_url = None
        if clip_ref:
            base = (clip_ref.get("filename") or "").rsplit(".", 1)[0]
            if base:
                snapshot_url = (
                    "/api/v1/recordings/"
                    + clip_ref.get("camera_id", "")
                    + "/"
                    + clip_ref.get("date", "")
                    + "/"
                    + base
                    + ".jpg"
                )
        return {
            "alert_id": "motion:" + evt_dict.get("id", ""),
            "camera_id": evt_dict.get("camera_id", ""),
            "camera_name": getattr(cam, "name", "") or evt_dict.get("camera_id", ""),
            "started_at": evt_dict.get("started_at", ""),
            "duration_seconds": evt_dict.get("duration_seconds", 0),
            "snapshot_url": snapshot_url,
            "deep_link": "/events/" + evt_dict.get("id", ""),
        }

    def _wire_throttle(
        self,
        event: dict,
        cameras_by_id: dict,
        prefs: dict,
        user_obj,
    ) -> dict | None:
        """Shape a throttle-transition audit event for OS notifications."""
        parsed = self._parse_throttle_detail(event.get("detail") or "")
        if parsed is None:
            return None
        camera_id, labels = parsed
        cam = cameras_by_id.get(camera_id)
        if cam is not None and not self._effective_rule(cam, prefs).get(
            "enabled",
            True,
        ):
            return None

        override = (prefs.get("cameras") or {}).get(camera_id)
        quiet_override = (
            override.get("quiet_schedule")
            if isinstance(override, dict) and "quiet_schedule" in override
            else None
        )
        event_time = self._parse_timestamp(
            event.get("timestamp") or ""
        ) or datetime.now(UTC)
        decision = evaluate_quiet_hours(
            now=event_time,
            user_schedule=getattr(user_obj, "notification_schedule", None) or [],
            camera_override=quiet_override,
            tz=self._current_timezone(),
        )
        if decision.quiet:
            self._emit_quiet_audit_ref(
                user=user_obj,
                camera_id=camera_id,
                event_class="throttle",
                reference=f"alert_id={self._audit_alert_id(event)}",
                now=datetime.now(UTC),
                window_key=decision.window_key,
                source=decision.source,
            )
            return None

        camera_name = getattr(cam, "name", "") if cam is not None else ""
        camera_name = camera_name or camera_id
        labels_text = ", ".join(labels) if labels else "See dashboard"
        return {
            "alert_id": self._audit_alert_id(event),
            "camera_id": camera_id,
            "camera_name": camera_name,
            "started_at": event.get("timestamp") or "",
            "duration_seconds": None,
            "snapshot_url": None,
            "deep_link": "/dashboard#camera-" + camera_id,
            "title": "Camera health warning: " + camera_name,
            "body": "Raspberry Pi throttling detected: " + labels_text,
        }

    @staticmethod
    def _parse_throttle_detail(detail: str) -> tuple[str, list[str]] | None:
        """Parse the throttle-transition audit detail string."""
        match = _THROTTLE_AUDIT_RE.match(detail.strip())
        if match is None:
            return None
        labels = [part.strip() for part in match.group("labels").split(",")]
        return match.group("camera_id"), [label for label in labels if label]

    @staticmethod
    def _audit_alert_id(event: dict) -> str:
        """Return a stable id for an audit-derived throttle notification."""
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
        return "throttle:" + hashlib.sha256(payload.encode()).hexdigest()[:16]
