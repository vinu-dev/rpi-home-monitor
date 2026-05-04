# REQ: SWR-001, SWR-009; RISK: RISK-002, RISK-020; SEC: SC-001, SC-020; TEST: TC-004, TC-049
"""Server-side session inventory and revocation service.

Keeps an enumerable list of authenticated browser sessions in
``/data/config/sessions.json`` while the Flask signed session cookie
continues to carry the caller's identity and CSRF state.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from monitor.models import ActiveSession

log = logging.getLogger("monitor.services.session_service")

LEGACY_SESSION_ID = "legacy-current"
ABSOLUTE_SESSION_SECONDS = 24 * 60 * 60
USER_AGENT_MAX_BYTES = 512
DEFAULT_CACHE_TTL_SECONDS = 10
DEFAULT_TOUCH_FLUSH_SECONDS = 10


@dataclass
class _CacheEntry:
    record: ActiveSession
    loaded_at: float
    last_persisted_active: float
    dirty: bool = False


class SessionService:
    """Issue, enumerate, touch, revoke, and expire active sessions."""

    def __init__(
        self,
        store,
        audit=None,
        *,
        idle_timeout_provider=None,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        touch_flush_seconds: int = DEFAULT_TOUCH_FLUSH_SECONDS,
    ):
        self._store = store
        self._audit = audit
        self._idle_timeout_provider = idle_timeout_provider or (lambda: 60)
        self._cache_ttl_seconds = max(1, int(cache_ttl_seconds))
        self._touch_flush_seconds = max(1, int(touch_flush_seconds))
        self._lock = threading.Lock()
        self._cache: dict[str, _CacheEntry] = {}

    def issue(
        self, user, *, source_ip: str = "", user_agent: str = ""
    ) -> ActiveSession:
        """Create and persist a fresh session row for ``user``."""
        now = time.time()
        for _ in range(3):
            session_id = secrets.token_urlsafe(32)
            if self._store.get_session(session_id) is not None:
                continue
            record = ActiveSession(
                id=session_id,
                user_id=user.id,
                username=user.username,
                role=user.role,
                created_at=now,
                last_active=now,
                expires_at=now + ABSOLUTE_SESSION_SECONDS,
                source_ip=source_ip or "",
                user_agent=_truncate_user_agent(user_agent),
                is_remember_me=False,
            )
            self._store.save_session(record)
            with self._lock:
                self._cache[session_id] = _CacheEntry(
                    record=_copy_session(record),
                    loaded_at=now,
                    last_persisted_active=record.last_active,
                )
            return _copy_session(record)
        raise RuntimeError("Unable to allocate a unique session id")

    def get(
        self, session_id: str, *, validate_timeout: bool = True
    ) -> ActiveSession | None:
        """Return one session row, optionally expiring it on demand."""
        if not session_id:
            return None
        now = time.time()
        record = self._get_cached_or_load(session_id, now)
        if record is None:
            return None
        if validate_timeout and self._is_expired(record, now):
            self._expire(record, now)
            return None
        return _copy_session(record)

    def touch(self, session_id: str) -> bool:
        """Refresh ``last_active`` with bounded write amplification."""
        if not session_id:
            return False
        now = time.time()
        with self._lock:
            entry = self._cache.get(session_id)
            if entry and now - entry.loaded_at <= self._cache_ttl_seconds:
                record = entry.record
            else:
                record = self._store.get_session(session_id)
                if record is None:
                    self._cache.pop(session_id, None)
                    return False
                entry = _CacheEntry(
                    record=_copy_session(record),
                    loaded_at=now,
                    last_persisted_active=record.last_active,
                )
                self._cache[session_id] = entry

            if self._is_expired(record, now):
                self._cache.pop(session_id, None)
                self._store.delete_session(session_id)
                self._log_event(
                    "SESSION_EXPIRED",
                    user=record.username,
                    ip=record.source_ip,
                    detail=(
                        f"user_id={record.user_id} session={_session_prefix(record.id)} "
                        "expired while refreshing activity"
                    ),
                )
                return False

            record.last_active = now
            entry.loaded_at = now
            if now - entry.last_persisted_active >= self._touch_flush_seconds:
                self._store.save_session(record)
                entry.last_persisted_active = now
                entry.dirty = False
            else:
                entry.dirty = True
            return True

    def revoke(
        self,
        session_id: str,
        *,
        actor_user: str,
        actor_role: str,
        actor_ip: str,
    ) -> ActiveSession | None:
        """Delete one session row and emit the appropriate audit event."""
        record = self.get(session_id)
        if record is None:
            return None
        deleted = self._store.delete_session(session_id)
        with self._lock:
            self._cache.pop(session_id, None)
        if not deleted:
            return None

        event = "SESSION_REVOKED"
        if actor_role == "admin" and actor_user and actor_user != record.username:
            event = "ADMIN_SESSION_REVOKED"
        self._log_event(
            event,
            user=actor_user,
            ip=actor_ip,
            detail=(
                f"target_user={record.username} target_session={_session_prefix(record.id)} "
                f"source_ip={record.source_ip}"
            ),
        )
        return record

    def discard(self, session_id: str) -> None:
        """Delete a session row without emitting audit noise."""
        if not session_id:
            return
        self._store.delete_session(session_id)
        with self._lock:
            self._cache.pop(session_id, None)

    def expire(
        self,
        session_id: str,
        *,
        fallback_user: str = "",
        fallback_ip: str = "",
        detail: str = "session timeout",
    ) -> None:
        """Expire one session row and emit a single timeout audit entry."""
        record = self.get(session_id, validate_timeout=False)
        if record is None:
            return
        self._store.delete_session(session_id)
        with self._lock:
            self._cache.pop(session_id, None)
        self._log_event(
            "SESSION_EXPIRED",
            user=record.username or fallback_user,
            ip=record.source_ip or fallback_ip,
            detail=f"user_id={record.user_id} session={_session_prefix(record.id)} {detail}",
        )

    def revoke_others(
        self,
        user_id: str,
        *,
        except_session_id: str,
        actor_user: str,
        actor_ip: str,
    ) -> int:
        """Delete every session for ``user_id`` except one preserved id."""
        deleted = self._store.delete_sessions_for_user(
            user_id,
            except_session_id=except_session_id,
        )
        if except_session_id:
            deleted = [row for row in deleted if row.id != except_session_id]
        with self._lock:
            for row in deleted:
                self._cache.pop(row.id, None)
        self._log_event(
            "SESSION_OTHERS_REVOKED",
            user=actor_user,
            ip=actor_ip,
            detail=f"user_id={user_id} revoked_count={len(deleted)}",
        )
        return len(deleted)

    def delete_sessions_for_user(self, user_id: str) -> int:
        """Remove all sessions for a deleted account."""
        deleted = self._store.delete_sessions_for_user(user_id)
        with self._lock:
            for row in deleted:
                self._cache.pop(row.id, None)
        return len(deleted)

    def list_sessions(
        self,
        *,
        requesting_user_id: str,
        current_session_id: str = "",
        include_all: bool = False,
    ) -> list[dict]:
        """Return serializable rows sorted with the current session first."""
        now = time.time()
        sessions = self._snapshot(now)
        if not include_all:
            sessions = [row for row in sessions if row.user_id == requesting_user_id]

        locked_user_ids = _locked_user_ids(self._store.get_users(), now)
        rows = [
            _serialize_session(
                session,
                current_session_id=current_session_id,
                locked_out=session.user_id in locked_user_ids,
            )
            for session in sessions
        ]
        rows.sort(
            key=lambda row: (
                0 if row["is_current"] else 1,
                -_timestamp_or_zero(row["last_active"]),
                row["username"],
            )
        )
        return rows

    def legacy_row(
        self, session_data: dict, *, source_ip: str = "", user_agent: str = ""
    ) -> dict:
        """Represent the currently loaded legacy signed-cookie session."""
        created_at = _coerce_float(session_data.get("created_at"))
        last_active = _coerce_float(session_data.get("last_active"))
        return {
            "id": LEGACY_SESSION_ID,
            "user_id": str(session_data.get("user_id") or ""),
            "username": str(session_data.get("username") or ""),
            "role": str(session_data.get("role") or "viewer"),
            "created_at": _isoformat_ts(created_at),
            "last_active": _isoformat_ts(last_active),
            "expires_at": _isoformat_ts(created_at + ABSOLUTE_SESSION_SECONDS)
            if created_at
            else "",
            "source_ip": source_ip or "",
            "user_agent": _truncate_user_agent(user_agent),
            "user_agent_parsed": _parse_user_agent(user_agent),
            "is_current": True,
            "is_legacy": True,
            "is_locked_out": False,
        }

    def clear_cache(self) -> None:
        """Test helper: drop all cached rows."""
        with self._lock:
            self._cache.clear()

    def _get_cached_or_load(self, session_id: str, now: float) -> ActiveSession | None:
        with self._lock:
            entry = self._cache.get(session_id)
            if entry and now - entry.loaded_at <= self._cache_ttl_seconds:
                return entry.record
        record = self._store.get_session(session_id)
        if record is None:
            with self._lock:
                self._cache.pop(session_id, None)
            return None
        with self._lock:
            self._cache[session_id] = _CacheEntry(
                record=_copy_session(record),
                loaded_at=now,
                last_persisted_active=record.last_active,
            )
            return self._cache[session_id].record

    def _expire(self, record: ActiveSession, now: float) -> None:
        self._store.delete_session(record.id)
        with self._lock:
            self._cache.pop(record.id, None)
        self._log_event(
            "SESSION_EXPIRED",
            user=record.username,
            ip=record.source_ip,
            detail=(
                f"user_id={record.user_id} session={_session_prefix(record.id)} "
                f"expired_at={_isoformat_ts(now)}"
            ),
        )

    def _is_expired(self, record: ActiveSession, now: float) -> bool:
        idle_timeout_seconds = max(1, int(self._idle_timeout_provider() or 60)) * 60
        if record.last_active and (now - record.last_active) > idle_timeout_seconds:
            return True
        if record.expires_at and now > record.expires_at:
            return True
        return bool(
            record.created_at and (now - record.created_at) > ABSOLUTE_SESSION_SECONDS
        )

    def _snapshot(self, now: float) -> list[ActiveSession]:
        sessions = {row.id: row for row in self._store.get_sessions()}
        expired: dict[str, ActiveSession] = {}
        for record in list(sessions.values()):
            if self._is_expired(record, now):
                expired[record.id] = _copy_session(record)
        with self._lock:
            for session_id, entry in list(self._cache.items()):
                if self._is_expired(entry.record, now):
                    expired[session_id] = _copy_session(entry.record)
                    continue
                sessions[session_id] = _copy_session(entry.record)
        for record in expired.values():
            self._expire(record, now)
            sessions.pop(record.id, None)
        return list(sessions.values())

    def _log_event(
        self, event: str, *, user: str = "", ip: str = "", detail: str = ""
    ) -> None:
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Audit log failed for %s: %s", event, exc)


def _copy_session(record: ActiveSession) -> ActiveSession:
    return ActiveSession(**asdict(record))


def _truncate_user_agent(user_agent: str) -> str:
    raw = (user_agent or "").encode("utf-8", errors="ignore")[:USER_AGENT_MAX_BYTES]
    return raw.decode("utf-8", errors="ignore")


def _isoformat_ts(value: float) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _timestamp_or_zero(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _locked_user_ids(users, now: float) -> set[str]:
    locked: set[str] = set()
    current = datetime.fromtimestamp(now, UTC)
    for user in users:
        locked_until = getattr(user, "locked_until", "") or ""
        if not locked_until:
            continue
        try:
            if datetime.fromisoformat(locked_until.replace("Z", "+00:00")) > current:
                locked.add(user.id)
        except ValueError:
            continue
    return locked


def _serialize_session(
    record: ActiveSession,
    *,
    current_session_id: str,
    locked_out: bool,
) -> dict:
    return {
        "id": record.id,
        "user_id": record.user_id,
        "username": record.username,
        "role": record.role,
        "created_at": _isoformat_ts(record.created_at),
        "last_active": _isoformat_ts(record.last_active),
        "expires_at": _isoformat_ts(record.expires_at),
        "source_ip": record.source_ip,
        "user_agent": record.user_agent,
        "user_agent_parsed": _parse_user_agent(record.user_agent),
        "is_current": bool(current_session_id and record.id == current_session_id),
        "is_legacy": False,
        "is_locked_out": locked_out,
    }


def _parse_user_agent(user_agent: str) -> dict[str, str]:
    raw = _truncate_user_agent(user_agent)
    browser = _browser_label(raw)
    os_name = _os_label(raw)
    return {"browser": browser, "os": os_name}


def _browser_label(user_agent: str) -> str:
    patterns = (
        (r"Edg/([0-9.]+)", "Edge"),
        (r"Firefox/([0-9.]+)", "Firefox"),
        (r"Chrome/([0-9.]+)", "Chrome"),
        (r"Version/([0-9.]+).*Safari/", "Safari"),
    )
    for pattern, label in patterns:
        match = re.search(pattern, user_agent)
        if match:
            version = match.group(1).split(".", 1)[0]
            return f"{label} {version}"
    return "Unknown browser"


def _os_label(user_agent: str) -> str:
    checks = (
        (r"Android ([0-9.]+)", "Android"),
        (r"iPhone OS ([0-9_]+)", "iOS"),
        (r"CPU OS ([0-9_]+)", "iOS"),
        (r"Mac OS X ([0-9_]+)", "macOS"),
        (r"Windows NT ([0-9.]+)", "Windows"),
    )
    for pattern, label in checks:
        match = re.search(pattern, user_agent)
        if match:
            version = match.group(1).replace("_", ".")
            major = version.split(".", 1)[0]
            return f"{label} {major}"
    if "Linux" in user_agent:
        return "Linux"
    return "Unknown OS"


def _session_prefix(session_id: str) -> str:
    return session_id[:8]
