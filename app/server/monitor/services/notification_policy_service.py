# REQ: SWR-033, SWR-041; RISK: RISK-016; SEC: SC-015; TEST: TC-031
"""
NotificationPolicyService — derive browser-notification eligibility for
motion events.

Implements ADR-0027 (#121, #128). The persistent triage surface is the
alert center (ADR-0024); this service decides which of those alerts ALSO
warrant an OS-level browser notification, applies per-camera filtering
and coalescing, and tracks which notifications a user has already
acknowledged via the polling client.

Decision tree per motion event (called from MotionEventStore phase=end
hook + on every polling fetch):

  1. event.peak_score < MOTION_NOTIFICATION_THRESHOLD
     → already filtered by AlertCenterService; not surfaced here either.
  2. event.duration_seconds < camera.notification_rule.min_duration_seconds
     → drop. Sub-3s default is empirically the noise floor.
  3. user.notification_prefs.enabled is False
     → drop (global per-user opt-out).
  4. camera.notification_rule.enabled is False (with per-user override)
     → drop (per-camera mute).
  5. now - camera.last_notification_at < camera coalesce window
     → suppress (event still in inbox; just no OS-level surface).
  6. event.started_at <= user.last_notification_seen_at
     → already delivered to this browser session; don't re-fire.
  7. Otherwise → eligible. Stamp camera.last_notification_at = now;
     return.

Concurrency: the service holds no in-memory state of its own. All
state lives on Camera + User records via Store. The store is the
serialisation point; concurrent updates to last_notification_at are
last-writer-wins, which matches the existing pattern (ADR-0024 read
state, #136 offline cooldown).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

# Imported up-top so ruff doesn't complain about module-level imports
# below code. Re-exporting AlertCenterService's MOTION_NOTIFICATION_THRESHOLD
# constant rather than duplicating it keeps the noise-floor in lockstep —
# sub-0.05 motion is filtered upstream by the alert center, so the policy
# service relies on the same value.
from monitor.services.alert_center_service import MOTION_NOTIFICATION_THRESHOLD

log = logging.getLogger("monitor.notification_policy")

# Hard caps on the per-camera tunables. Enforced at PUT time so the
# UI can't store nonsense that breaks the decision tree. Documented
# in ADR-0027 §"Resolved open questions" — operators tune within
# these bounds.
MIN_DURATION_LO = 1
MIN_DURATION_HI = 60
COALESCE_LO = 10
COALESCE_HI = 600


class NotificationPolicyService:
    """Derive notification eligibility per motion event.

    Constructor uses the service-layer DI pattern (ADR-0003); no I/O
    in __init__.

    Args:
        store: Server-side data store — used to read Camera + User
            records and persist last_notification_at on Camera.
        motion_event_store: For looking up motion events by id.
    """

    def __init__(self, *, store, motion_event_store):
        self._store = store
        self._motion = motion_event_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_for_user(
        self, *, user: str, since: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Return surfaceable motion notifications for ``user`` newer
        than ``since`` (ISO-8601 Z), capped at ``limit``.

        Each entry is the wire shape consumed by
        ``GET /api/v1/notifications/pending``.
        """
        user_obj = self._get_user(user)
        if user_obj is None:
            return []
        prefs = self._user_prefs(user_obj)
        if not prefs.get("enabled", False):
            return []

        # Bound the lookback to the user's last-seen pointer so the
        # client can't accidentally request the entire history.
        since_iso = (
            since
            or prefs.get("last_notification_seen_at", "")
            or user_obj.last_notification_seen_at
        )

        try:
            events = self._motion.list_events(limit=200)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("notification_policy: motion fetch failed: %s", exc)
            return []

        cameras_by_id = {}
        try:
            for c in self._store.get_cameras():
                cameras_by_id[c.id] = c
        except Exception as exc:  # pragma: no cover
            log.warning("notification_policy: cameras fetch failed: %s", exc)
            return []

        out: list[dict] = []
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

            out.append(self._wire(evt, cam))
            if len(out) >= limit:
                break

        return out

    def mark_seen(self, *, user: str, alert_ids: list[str]) -> int:
        """Advance ``user.last_notification_seen_at`` to the newest
        delivered alert, persist. Returns the count actually marked
        (i.e. those whose id maps to a known motion event).
        """
        user_obj = self._get_user(user)
        if user_obj is None or not alert_ids:
            return 0
        latest = ""
        marked = 0
        for aid in alert_ids:
            evt_id = aid.removeprefix("motion:") if aid.startswith("motion:") else aid
            try:
                evt = self._motion.get(evt_id)
            except Exception:
                evt = None
            if evt is None:
                continue
            ts = getattr(evt, "started_at", "")
            if ts and ts > latest:
                latest = ts
            marked += 1
        if latest and latest > (user_obj.last_notification_seen_at or ""):
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
            return {"enabled": False, "cameras": {}}
        return self._user_prefs(user_obj)

    def update_prefs(self, *, user: str, payload: dict) -> tuple[dict, str]:
        """Validate + persist a partial-update of the user's prefs.

        Returns ``(new_prefs, error_str)``. ``error_str`` is empty on
        success.
        """
        user_obj = self._get_user(user)
        if user_obj is None:
            return {}, "user not found"
        prefs = dict(self._user_prefs(user_obj))

        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                return {}, "enabled must be a boolean"
            prefs["enabled"] = payload["enabled"]

        if "cameras" in payload:
            cams = payload["cameras"]
            if not isinstance(cams, dict):
                return {}, "cameras must be an object"
            new_cameras: dict = {}
            for cam_id, override in cams.items():
                if not isinstance(cam_id, str):
                    return {}, "cameras keys must be strings"
                if override is None:
                    # null override = remove → camera-level default
                    # applies. Don't carry the entry forward.
                    continue
                if not isinstance(override, dict):
                    return {}, f"cameras[{cam_id}] must be an object or null"
                clean: dict = {}
                if "enabled" in override:
                    if override["enabled"] is None:
                        pass  # explicit null → drop
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
                if clean:
                    new_cameras[cam_id] = clean
            prefs["cameras"] = new_cameras

        user_obj.notification_prefs = prefs
        try:
            self._store.save_user(user_obj)
        except Exception as exc:  # pragma: no cover
            log.warning("notification_policy: save_user failed: %s", exc)
            return {}, "internal error"
        return prefs, ""

    # ------------------------------------------------------------------
    # Decision tree internals
    # ------------------------------------------------------------------

    def _eligible(self, evt, cam, user_obj, prefs: dict) -> bool:
        """Apply the ADR-0027 §"Decision tree" filter chain.

        Side effect: on success, stamps ``cam.last_notification_at``
        and persists the camera record so the coalesce window
        survives across calls.
        """
        peak_score = float(getattr(evt, "peak_score", 0.0) or 0.0)
        if peak_score < MOTION_NOTIFICATION_THRESHOLD:
            return False

        # Effective per-camera rule = camera default + per-user override
        rule = self._effective_rule(cam, prefs)
        if not rule.get("enabled", True):
            return False

        duration = float(getattr(evt, "duration_seconds", 0.0) or 0.0)
        if duration < rule.get("min_duration_seconds", 3):
            return False

        # Coalesce: don't surface the same camera twice within the
        # window. Read the stamp from the camera record (not in-memory)
        # so a polling client that reconnects mid-coalesce still
        # respects it.
        last_at = getattr(cam, "last_notification_at", "") or ""
        if last_at:
            try:
                last_dt = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
                now = datetime.now(UTC)
                elapsed = (now - last_dt).total_seconds()
                if elapsed < rule.get("coalesce_seconds", 60):
                    return False
            except (ValueError, TypeError):
                # Corrupt stamp — fail open and emit; same fail-open
                # discipline as #136's offline-alert cooldown.
                pass

        # All gates passed. Stamp + persist.
        cam.last_notification_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
            for k in ("enabled", "min_duration_seconds", "coalesce_seconds"):
                if k in override and override[k] is not None:
                    result[k] = override[k]
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_user(self, username: str):
        """Best-effort user lookup. Some stores expose ``get_user`` by
        username; some by id. Try both. Return None when not found."""
        try:
            users = list(self._store.get_users() or [])
        except Exception:
            return None
        for u in users:
            if getattr(u, "username", None) == username:
                return u
        return None

    @staticmethod
    def _user_prefs(user_obj) -> dict:
        """Return the user's prefs with safe defaults for legacy
        records that pre-date the notification_prefs field."""
        prefs = getattr(user_obj, "notification_prefs", None) or {}
        return {
            "enabled": bool(prefs.get("enabled", False)),
            "cameras": dict(prefs.get("cameras", {}) or {}),
            "last_notification_seen_at": (
                getattr(user_obj, "last_notification_seen_at", "") or ""
            ),
        }

    @staticmethod
    def _validate_int(value: Any, lo: int, hi: int, label: str) -> str:
        """Validate an int range; allow None for "use default."""
        if value is None:
            return ""
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{label} must be an integer"
        if value < lo or value > hi:
            return f"{label} must be {lo}..{hi}"
        return ""

    def _wire(self, evt, cam) -> dict:
        """Shape a motion event + its correlated clip into the
        wire format the polling client consumes."""
        evt_dict = asdict(evt) if hasattr(evt, "__dataclass_fields__") else dict(evt)
        clip_ref = evt_dict.get("clip_ref") or None
        snapshot_url = None
        if clip_ref:
            # `<recordings>/<cam>/<date>/<filename>.mp4` → sibling
            # `.jpg`. The actual extraction is best-effort and lives
            # in monitor.services.snapshot_extractor; here we only
            # produce the URL the client should request.
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
