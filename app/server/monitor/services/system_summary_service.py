"""
System summary service — the Tier-1 dashboard aggregator (ADR-0018).

Produces **derived state**, not raw metrics. The rule from ADR-0018:

    Raw metrics belong on /diagnostics. Derived state belongs on the
    dashboard. A number without a threshold is decoration.

So this service does not return "CPU is at 87 %". It returns
``state = "amber"``, a one-sentence ``summary``, and a ``deep_link`` to
whichever page lets the user act on the condition.

Thresholds are locked constants — see the ADR for why.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

log = logging.getLogger("monitor.system_summary")


# ---------------------------------------------------------------------------
# Thresholds (LOCKED — see ADR-0018). Changing these degrades user trust in
# the status-strip colour. Any change must go through a new ADR.
# ---------------------------------------------------------------------------

DISK_AMBER_PERCENT = 70
DISK_RED_PERCENT = 90

CPU_AMBER_PERCENT = 85  # recorder host CPU, sustained
CPU_TEMP_AMBER_C = 72  # recorder host SoC temperature
CPU_TEMP_RED_C = 80
MEMORY_AMBER_PERCENT = 85

CAMERA_OFFLINE_AMBER_SECONDS = 60  # < 1 h: amber
CAMERA_OFFLINE_RED_SECONDS = 60 * 60  # >= 1 h: red

ERROR_WINDOW_SECONDS = 60 * 60  # "errors in last hour"

RETENTION_SAMPLE_DAYS = 7  # trailing window for write-rate
# Cache TTL for the retention estimate. Walking the recordings tree is
# expensive (O(files)) and retention only drifts on the order of hours
# with typical write rates; 5 minutes is a safe compromise between
# freshness and dashboard responsiveness.
RETENTION_CACHE_TTL_SECONDS = 300

# Audit events treated as error-level for the status strip. Kept narrow so a
# noisy login-failed storm (expected during a password-spray) doesn't flip
# the dashboard red.
ERROR_EVENT_TYPES = frozenset(
    {
        "OTA_FAILED",
        "OTA_ROLLBACK",
        "CAMERA_REMOVED_UNEXPECTEDLY",
        "FIRMWARE_INSTALL_FAILED",
        "RECORDER_CRASH",
        "STORAGE_FAILED",
        "CERT_RENEWAL_FAILED",
    }
)

WARNING_EVENT_TYPES = frozenset(
    {
        "CAMERA_OFFLINE",
        "STORAGE_THRESHOLD_EXCEEDED",
        "CERT_EXPIRING_SOON",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_ts(value: str | None) -> datetime | None:
    """Parse an ISO-8601 audit/heartbeat timestamp. Returns None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


_SEVERITY_RANKS = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def _sev_rank(severity: str) -> int:
    """Numeric rank of a fault severity string. Higher = worse."""
    return _SEVERITY_RANKS.get(severity, 0)


def _worst(*states: str) -> str:
    """Return the worst state of the supplied set (red > amber > green)."""
    order = {"green": 0, "amber": 1, "red": 2}
    best_seen = "green"
    for s in states:
        if order.get(s, -1) > order[best_seen]:
            best_seen = s
    return best_seen


# ---------------------------------------------------------------------------
# SystemSummaryService
# ---------------------------------------------------------------------------


class SystemSummaryService:
    """Aggregate signals into the Tier-1 dashboard status strip payload.

    Dependencies are passed explicitly (service-layer pattern, ADR-0003).
    No I/O in ``__init__`` — all work happens in ``compute_summary()``.
    """

    def __init__(
        self,
        *,
        store,
        storage_manager,
        audit,
        recordings_service,
        health_module,
    ):
        self._store = store
        self._storage = storage_manager
        self._audit = audit
        self._recordings = recordings_service
        # Injected so tests can stub host metrics without touching /proc.
        self._health = health_module
        # Retention estimate walks the entire recordings tree (rglob +
        # stat per file). That is ~O(thousands of files) and was firing
        # on every /system/summary call — which the dashboard polls
        # every 10 s, so each tab switch stalled on the walk. The
        # walk's result barely moves minute-to-minute, so cache it for
        # RETENTION_CACHE_TTL seconds and serve stale for that window.
        self._retention_cache: tuple[float, float | None] | None = None

    # -- public API ---------------------------------------------------------

    def compute_summary(self) -> dict:
        """Return the dashboard status-strip payload.

        Shape:
            {
              "state": "green" | "amber" | "red",
              "summary": "...",              # one sentence, human-readable
              "details": {
                "cameras": {"online": N, "total": M, "offline_names": [...]},
                "storage": {"percent": X, "retention_days": D | None},
                "recorder": {"cpu_percent": X, "cpu_temp_c": T,
                              "memory_percent": M},
                "recent_errors": N,
              },
              "deep_link": "/..."            # where to go to act on it
            }

        This method must never raise. Any sub-signal failure degrades to
        a neutral value for that signal; the rest of the payload is still
        delivered so the dashboard renders something useful.
        """
        cam_state, cam_detail, cam_link = self._cameras()
        disk_state, disk_detail, disk_link = self._storage_state()
        host_state, host_detail, host_link = self._recorder_host()
        err_state, err_count, err_link = self._recent_errors()

        state = _worst(cam_state, disk_state, host_state, err_state)

        summary, deep_link = self._build_summary(
            state,
            cam_state=cam_state,
            cam_detail=cam_detail,
            cam_link=cam_link,
            disk_state=disk_state,
            disk_detail=disk_detail,
            disk_link=disk_link,
            host_state=host_state,
            host_link=host_link,
            err_state=err_state,
            err_count=err_count,
            err_link=err_link,
        )

        return {
            "state": state,
            "summary": summary,
            "deep_link": deep_link,
            "details": {
                "cameras": cam_detail,
                "storage": disk_detail,
                "recorder": host_detail,
                "recent_errors": err_count,
            },
        }

    # -- cameras ------------------------------------------------------------

    def _cameras(self) -> tuple[str, dict, str]:
        try:
            cameras = self._store.get_cameras()
        except Exception as exc:
            log.warning("summary: get_cameras failed: %s", exc)
            return "green", {"online": 0, "total": 0, "offline_names": []}, "/"

        # ADR-0018 counts paired cameras only (pending are mid-onboarding).
        paired = [c for c in cameras if getattr(c, "status", "") != "pending"]
        online = [c for c in paired if getattr(c, "status", "") == "online"]
        offline = [c for c in paired if getattr(c, "status", "") == "offline"]

        now = datetime.now(UTC)
        worst = "green"
        for cam in offline:
            last_seen = _parse_ts(getattr(cam, "last_seen", None))
            if last_seen is None:
                # Never seen → treat as long-offline.
                worst = _worst(worst, "red")
                continue
            elapsed = (now - last_seen).total_seconds()
            if elapsed >= CAMERA_OFFLINE_RED_SECONDS:
                worst = _worst(worst, "red")
            elif elapsed >= CAMERA_OFFLINE_AMBER_SECONDS:
                worst = _worst(worst, "amber")

        # Hardware faults on online cameras count against the summary
        # even when the camera is reachable — "3/3 online" is still
        # misleading when two of them have no sensor. Promote to amber
        # for any ``warning``/``error`` fault, red for ``critical``.
        faulted = []
        for cam in online:
            faults = getattr(cam, "hardware_faults", None) or []
            if not faults:
                # Legacy path for cameras that haven't reported the
                # structured list yet — fall back to the v1.3.0 flag.
                if getattr(cam, "hardware_ok", True) is False:
                    faulted.append(cam)
                    worst = _worst(worst, "amber")
                continue
            faulted.append(cam)
            max_sev = max((f.get("severity", "warning") for f in faults), key=_sev_rank)
            if max_sev == "critical":
                worst = _worst(worst, "red")
            else:
                worst = _worst(worst, "amber")

        detail = {
            "online": len(online),
            "total": len(paired),
            "offline_names": [
                getattr(c, "name", "") or getattr(c, "id", "") for c in offline
            ],
            "faulted_names": [
                getattr(c, "name", "") or getattr(c, "id", "") for c in faulted
            ],
        }
        # Deep-link to the first offline or faulted camera if any,
        # else the dashboard itself. Use the explicit `/dashboard`
        # path (not `/`) — the index route 302s to /dashboard which
        # drops the URL fragment, so `/#camera-X` ends up on
        # /dashboard with no anchor and the click does nothing.
        # Pairs with the camera-card `id="camera-<id>"` anchor in
        # dashboard.html so same-page clicks scroll cleanly without
        # a navigation round-trip.
        link = "/dashboard"
        first_problem = offline[0] if offline else (faulted[0] if faulted else None)
        if first_problem is not None:
            link = "/dashboard#camera-" + getattr(first_problem, "id", "")
        return worst, detail, link

    # -- storage ------------------------------------------------------------

    def _storage_state(self) -> tuple[str, dict, str]:
        try:
            stats = self._storage.get_storage_stats()
        except Exception as exc:
            log.warning("summary: get_storage_stats failed: %s", exc)
            return "green", {"percent": 0.0, "retention_days": None}, "/settings"

        percent = float(stats.get("percent", 0.0) or 0.0)
        if percent >= DISK_RED_PERCENT:
            state = "red"
        elif percent >= DISK_AMBER_PERCENT:
            state = "amber"
        else:
            state = "green"

        retention = self._estimate_retention_days(stats)
        detail = {
            "percent": round(percent, 1),
            "retention_days": retention,
            "free_gb": stats.get("free_gb", 0),
            "total_gb": stats.get("total_gb", 0),
        }
        return state, detail, "/settings#storage"

    def _estimate_retention_days(self, stats: dict) -> float | None:
        """Retention = free_bytes / bytes_written_per_day (7-day trailing).

        Returns None if there is less than a day of data to average against —
        better to show "—" than a confidently wrong number.

        Cached for RETENTION_CACHE_TTL_SECONDS — the filesystem walk is
        O(files) and the estimate drifts very slowly in practice.
        """
        now = time.time()
        if self._retention_cache is not None:
            cached_at, cached_value = self._retention_cache
            if now - cached_at < RETENTION_CACHE_TTL_SECONDS:
                return cached_value

        value = self._compute_retention_days(stats)
        self._retention_cache = (now, value)
        return value

    def _compute_retention_days(self, stats: dict) -> float | None:
        rec_dir = stats.get("recordings_dir")
        free_gb = stats.get("free_gb", 0) or 0
        if not rec_dir or free_gb <= 0:
            return None

        from pathlib import Path

        root = Path(rec_dir)
        if not root.is_dir():
            return None

        cutoff = time.time() - RETENTION_SAMPLE_DAYS * 86400
        earliest = time.time()
        bytes_in_window = 0
        for mp4 in root.rglob("*.mp4"):
            try:
                st = mp4.stat()
            except OSError:
                continue
            if st.st_mtime >= cutoff:
                bytes_in_window += st.st_size
                if st.st_mtime < earliest:
                    earliest = st.st_mtime

        age_hours = (time.time() - earliest) / 3600
        if bytes_in_window <= 0 or age_hours < 24:
            return None

        days_in_sample = max(age_hours / 24, 1.0)
        bytes_per_day = bytes_in_window / days_in_sample
        if bytes_per_day <= 0:
            return None

        free_bytes = free_gb * (1024**3)
        return round(free_bytes / bytes_per_day, 1)

    # -- recorder host ------------------------------------------------------

    def _recorder_host(self) -> tuple[str, dict, str]:
        try:
            summary = self._health.get_health_summary(
                self._recordings.default_recordings_dir or "/data"
            )
        except Exception as exc:
            log.warning("summary: get_health_summary failed: %s", exc)
            return "green", {}, "/settings#system"

        cpu_temp = summary.get("cpu_temp_c", 0.0) or 0.0
        cpu_pct = summary.get("cpu_usage_percent", 0.0) or 0.0
        mem_pct = (summary.get("memory") or {}).get("percent", 0.0) or 0.0

        state = "green"
        if cpu_temp >= CPU_TEMP_RED_C:
            state = _worst(state, "red")
        elif cpu_temp >= CPU_TEMP_AMBER_C:
            state = _worst(state, "amber")
        if cpu_pct >= CPU_AMBER_PERCENT:
            state = _worst(state, "amber")
        if mem_pct >= MEMORY_AMBER_PERCENT:
            state = _worst(state, "amber")

        detail = {
            "cpu_percent": round(cpu_pct, 1),
            "cpu_temp_c": round(cpu_temp, 1),
            "memory_percent": round(mem_pct, 1),
        }
        return state, detail, "/settings#system"

    # -- recent errors ------------------------------------------------------

    def _recent_errors(self) -> tuple[str, int, str]:
        try:
            # Pull a wide page; we filter in-memory by time + type.
            events = self._audit.get_events(limit=500)
        except Exception as exc:
            log.warning("summary: audit.get_events failed: %s", exc)
            return "green", 0, "/logs"

        # Effective cutoff = max(1h-window, this-user's-last-seen).
        # The per-user "I've read the log" timestamp is stored on the
        # Flask session and stamped when they visit /logs — without it,
        # an event could show "1 recent system event" for up to an hour
        # even though the user has already reviewed the log.
        cutoff = datetime.now(UTC) - timedelta(seconds=ERROR_WINDOW_SECONDS)
        try:
            # Late import + optional — the summary service must not crash
            # outside a request context (e.g. startup self-tests).
            from flask import has_request_context, session

            if has_request_context():
                seen_iso = session.get("audit_seen_at")
                if seen_iso:
                    seen_dt = _parse_ts(seen_iso)
                    if seen_dt is not None and seen_dt > cutoff:
                        cutoff = seen_dt
        except Exception:
            pass

        errors = 0
        warnings = 0
        for ev in events:
            ts = _parse_ts(ev.get("timestamp"))
            if ts is None or ts < cutoff:
                continue
            ev_type = ev.get("event", "")
            if ev_type in ERROR_EVENT_TYPES:
                errors += 1
            elif ev_type in WARNING_EVENT_TYPES:
                warnings += 1

        state = "green"
        if errors > 0:
            state = "red"
        elif warnings > 0:
            state = "amber"
        return state, errors + warnings, "/logs"

    # -- summary sentence ---------------------------------------------------

    @staticmethod
    def _build_summary(
        state: str,
        *,
        cam_state: str,
        cam_detail: dict,
        cam_link: str,
        disk_state: str,
        disk_detail: dict,
        disk_link: str,
        host_state: str,
        host_link: str,
        err_state: str,
        err_count: int,
        err_link: str,
    ) -> tuple[str, str]:
        """Produce (sentence, deep_link) matching the overall state.

        Green is quiet (no "what's wrong" pointer). Amber/Red pick the
        single worst offender and deep-link to it — the dashboard does
        not try to enumerate every fault in the status strip; the tiles
        below do that.
        """
        if state == "green":
            online = cam_detail.get("online", 0)
            total = cam_detail.get("total", 0)
            disk_pct = disk_detail.get("percent", 0)
            return (
                f"All systems normal — {online}/{total} cameras online, "
                f"{disk_pct:.0f}% disk used"
            ), "/"

        # Priority of what to surface first: errors > red signals > amber.
        # Within the same severity, storage is most actionable.
        order = [
            (
                err_state,
                err_count > 0,
                f"{err_count} recent system event{'s' if err_count != 1 else ''} — review log",
                err_link,
            ),
            (
                disk_state,
                True,
                f"Recorder disk {disk_detail.get('percent', 0):.0f}% full",
                disk_link,
            ),
            (
                cam_state,
                cam_detail.get("offline_names") or cam_detail.get("faulted_names"),
                _camera_sentence(cam_detail),
                cam_link,
            ),
            (host_state, True, "Recorder under load — check host metrics", host_link),
        ]

        # Red first, then amber.
        for wanted in ("red", "amber"):
            for sig_state, present, sentence, link in order:
                if sig_state == wanted and present and sentence:
                    return sentence, link

        # Fallback (should not happen): overall state isn't green but no
        # sub-signal claimed it. Keep something meaningful on screen.
        return "System needs attention", "/settings"


def _camera_sentence(cam_detail: dict) -> str:
    names = cam_detail.get("offline_names") or []
    if not names:
        return ""
    if len(names) == 1:
        return f"{names[0]} is offline"
    return f"{len(names)} cameras are offline"
