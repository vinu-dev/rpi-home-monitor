# REQ: SWR-009; RISK: RISK-020; SEC: SC-008, SC-020; TEST: TC-017
"""
Audit log API — read and clear the security audit trail.

Powers the dashboard's "recent activity" log teaser (ADR-0018 Slice 3)
and the Settings > Security audit view. Write access stays private to
the services that emit events (pairing, OTA, user auth, clip delete);
the HTTP layer is admin-gated so a compromised low-priv session can't
exfiltrate login-failure patterns or erase the audit trail.

Endpoints:
  GET    /events  - most recent audit events (admin only)
  DELETE /events  - truncate the audit log (admin only; writes sentinel first)

Query params (GET only):
  limit       - 1..200, default 50
  event_type  - filter by exact event name (optional)
"""

import csv
import io
import json
import time
from datetime import UTC, datetime

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    request,
    session,
    stream_with_context,
)
from werkzeug.exceptions import ClientDisconnected

from monitor.auth import admin_required, csrf_protect

audit_bp = Blueprint("audit", __name__)

EXPORT_RATE_LIMIT_WINDOW = 60 * 60
EXPORT_RATE_LIMIT_MAX = 3
EXPORT_RATE_LIMIT_BLOCK = 6
_export_attempts_by_ip: dict[str, list[float]] = {}
_export_attempts_by_user: dict[str, list[float]] = {}


def _parse_export_timestamp(value: str, *, label: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"{label} must be ISO-8601 UTC (example: 2026-05-04T00:00:00Z)"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone and use UTC (Z)")
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _csrf_token_error():
    token = request.headers.get("X-CSRF-Token", "")
    if not token or token != session.get("csrf_token"):
        return jsonify({"error": "Invalid CSRF token"}), 403
    return None


def _check_export_limit(
    bucket: dict[str, list[float]], key: str
) -> tuple[bool, bool, int]:
    now = time.time()
    attempts = [t for t in bucket.get(key, []) if now - t < EXPORT_RATE_LIMIT_WINDOW]
    bucket[key] = attempts
    retry_after = (
        max(1, int(EXPORT_RATE_LIMIT_WINDOW - (now - attempts[0])))
        if attempts
        else EXPORT_RATE_LIMIT_WINDOW
    )
    count = len(attempts)
    if count >= EXPORT_RATE_LIMIT_BLOCK:
        return False, False, retry_after
    if count >= EXPORT_RATE_LIMIT_MAX:
        return True, True, retry_after
    return True, False, retry_after


def _record_export_attempt(bucket: dict[str, list[float]], key: str) -> None:
    bucket.setdefault(key, []).append(time.time())


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


def _csv_row(values: list[str]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(values)
    return buffer.getvalue()


def _export_filename(fmt: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"audit-{stamp}.{fmt}"


@audit_bp.route("/events", methods=["GET"])
@admin_required
def list_events():
    """Return recent audit events, newest-first."""
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))

    event_type = request.args.get("event_type", "").strip()

    try:
        events = current_app.audit.get_events(limit=limit, event_type=event_type)
    except Exception:  # pragma: no cover - defensive: never crash status strip
        events = []

    return jsonify({"events": events, "count": len(events)})


@audit_bp.route("/events/export", methods=["GET"])
@admin_required
def export_events():
    """Stream the audit log as CSV or JSON.

    GET is intentionally CSRF-guarded because the response contains the full
    audit history rather than an idempotent view fragment.
    """
    csrf_error = _csrf_token_error()
    if csrf_error is not None:
        return csrf_error

    export_format = request.args.get("format", "").strip().lower()
    if export_format not in {"csv", "json"}:
        return jsonify({"error": "format must be csv or json"}), 400

    try:
        start = _parse_export_timestamp(request.args.get("start", ""), label="start")
        end = _parse_export_timestamp(request.args.get("end", ""), label="end")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if start and end and start > end:
        return jsonify({"error": "start must be <= end"}), 400

    event_type = request.args.get("event_type", "").strip()
    actor = request.args.get("actor", "").strip()
    user = session.get("username", "")
    ip = request.remote_addr or ""

    allowed_user, warn_user, retry_user = _check_export_limit(
        _export_attempts_by_user, user
    )
    allowed_ip, warn_ip, retry_ip = _check_export_limit(_export_attempts_by_ip, ip)
    if not allowed_user or not allowed_ip:
        retry_after = max(retry_user, retry_ip)
        current_app.audit.log_event(
            "AUDIT_LOG_EXPORT_DENIED",
            user=user,
            ip=ip,
            detail=json.dumps(
                {
                    "format": export_format,
                    "filters": {
                        "start": start,
                        "end": end,
                        "event_type": event_type,
                        "actor": actor,
                    },
                    "reason": "rate_limited",
                    "retry_after": retry_after,
                },
                separators=(",", ":"),
            ),
        )
        response = jsonify({"error": "Export rate-limited. Try again later."})
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    _record_export_attempt(_export_attempts_by_user, user)
    _record_export_attempt(_export_attempts_by_ip, ip)

    filters = {
        "start": start,
        "end": end,
        "event_type": event_type,
        "actor": actor,
    }
    row_count = 0
    truncated = False
    reason = ""
    warned = warn_user or warn_ip

    def _entries():
        nonlocal row_count, truncated, reason
        try:
            iterator = current_app.audit.iter_events(
                start=start,
                end=end,
                event_type=event_type,
                actor=actor,
            )
            if export_format == "csv":
                yield _csv_row(["timestamp", "event", "user", "ip", "detail"])
                for entry in iterator:
                    row_count += 1
                    yield _csv_row(
                        [
                            _csv_cell(entry.get("timestamp", "")),
                            _csv_cell(entry.get("event", "")),
                            _csv_cell(entry.get("user", "")),
                            _csv_cell(entry.get("ip", "")),
                            _csv_cell(entry.get("detail", "")),
                        ]
                    )
            else:
                yield "["
                first = True
                for entry in iterator:
                    row_count += 1
                    if not first:
                        yield ","
                    yield json.dumps(entry, separators=(",", ":"))
                    first = False
                yield "]\n"
        except (BrokenPipeError, ClientDisconnected, GeneratorExit):
            truncated = True
            reason = "client_disconnect"
            raise
        except OSError:
            truncated = True
            reason = "io_error"
        finally:
            detail = {
                "format": export_format,
                "filters": filters,
                "row_count": row_count,
                "truncated": truncated,
            }
            if reason:
                detail["reason"] = reason
            if warned:
                detail["rate_limit_warning"] = True
            current_app.audit.log_event(
                "AUDIT_LOG_EXPORTED",
                user=user,
                ip=ip,
                detail=json.dumps(detail, separators=(",", ":")),
            )

    mimetype = "text/csv" if export_format == "csv" else "application/json"
    response = Response(stream_with_context(_entries()), mimetype=mimetype)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{_export_filename(export_format)}"'
    )
    return response


@audit_bp.route("/events", methods=["DELETE"])
@admin_required
@csrf_protect
def clear_events():
    """Truncate the audit log (admin only).

    Writes an AUDIT_LOG_CLEARED sentinel before truncating so chain of
    custody is preserved. Returns 200 with cleared=true on success.
    """
    ip = request.remote_addr or ""
    user = session.get("username", "")

    try:
        current_app.audit.clear_events(user=user, ip=ip)
    except Exception:  # pragma: no cover
        return jsonify({"cleared": False, "error": "internal error"}), 500

    return jsonify({"cleared": True})
