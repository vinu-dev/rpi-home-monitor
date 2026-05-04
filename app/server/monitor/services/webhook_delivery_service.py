# REQ: SWR-056, SWR-057; RISK: RISK-017, RISK-020, RISK-021; SEC: SC-012, SC-020, SC-021; TEST: TC-023, TC-041, TC-042, TC-048, TC-049
"""Outbound webhook configuration and delivery service."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import queue
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from monitor.models import WebhookDestination
from monitor.services.alert_center_service import MOTION_NOTIFICATION_THRESHOLD

log = logging.getLogger("monitor.webhooks")

EVENT_CLASS_CHOICES = frozenset(
    {"motion", "camera_offline", "storage_low", "ota_outcome"}
)
AUTH_TYPE_CHOICES = frozenset({"none", "bearer", "hmac"})
RETRY_DELAYS_SECONDS = (5, 8, 13)
REQUEST_TIMEOUT_SECONDS = 10
DEGRADED_FAILURE_THRESHOLD = 5
MAX_REDIRECTS = 2
MAX_HISTORY_SCAN = 1000
MAX_RESPONSE_EXCERPT = 200
RESERVED_HEADERS = frozenset(
    {
        "authorization",
        "content-length",
        "content-type",
        "host",
        "x-webhook-signature",
    }
)
DELIVERY_AUDIT_EVENTS = frozenset(
    {
        "WEBHOOK_DELIVERY_SUCCESS",
        "WEBHOOK_DELIVERY_FAILED",
        "WEBHOOK_DELIVERY_DEGRADED",
    }
)
OTA_OUTCOME_EVENTS = {
    "OTA_COMPLETED": ("info", "completed"),
    "OTA_FAILED": ("error", "failed"),
    "OTA_ROLLBACK": ("warning", "rollback"),
    "OTA_INSTALL_COMPLETE": ("info", "completed"),
    "OTA_INSTALL_FAILED": ("error", "failed"),
}
_MOTION_DETAIL_RE = re.compile(r"^(?P<camera_id>\S+)\s+event=(?P<event_id>\S+)")
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_PRIVATE_TARGET_ERROR = (
    "Webhook URL must be reachable from your network; local/private IPs are not allowed"
)
_HTTPS_ONLY_ERROR = "Webhook URL must use HTTPS"
_SENTINEL = object()


class WebhookConfigError(ValueError):
    """Raised when a destination cannot be used safely."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class DeliveryTask:
    destination_id: str
    event_class: str
    payload: dict


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    headers: dict[str, str]
    body_text: str
    final_url: str


@dataclass(frozen=True)
class DeliveryResult:
    destination_id: str
    event_type: str
    status_code: int | None
    latency_ms: int
    delivered: bool
    attempt: int
    will_retry: bool
    error: str
    response_excerpt: str
    timestamp: str
    url: str

    def as_history_entry(self) -> dict:
        return {
            "destination_id": self.destination_id,
            "event_type": self.event_type,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "delivered": self.delivered,
            "attempt": self.attempt,
            "will_retry": self.will_retry,
            "error": self.error,
            "response_excerpt": self.response_excerpt,
            "timestamp": self.timestamp,
            "url": self.url,
        }


class WebhookDeliveryService:
    """Manage webhook destinations and deliver matching events."""

    def __init__(
        self,
        *,
        store,
        audit,
        motion_event_store,
        worker_count: int = 4,
        http_client: Callable[[str, bytes, dict[str, str], int], HttpResult]
        | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        autostart: bool = True,
    ):
        self._store = store
        self._audit = audit
        self._motion = motion_event_store
        self._worker_count = max(1, int(worker_count))
        self._http_client = http_client or _default_http_post
        self._sleep = sleep_fn or time.sleep
        self._queue: queue.Queue[DeliveryTask | object] = queue.Queue()
        self._settings_lock = threading.Lock()
        self._locks_lock = threading.Lock()
        self._destination_locks: dict[str, threading.Lock] = {}
        self._workers: list[threading.Thread] = []
        self._running = False
        if self._audit is not None and hasattr(self._audit, "add_listener"):
            self._audit.add_listener(self.handle_audit_entry)
        if autostart:
            self.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for index in range(self._worker_count):
            worker = threading.Thread(
                target=self._worker,
                name=f"webhook-delivery-{index}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

    def close(self) -> None:
        if not self._running:
            return
        self._running = False
        for _ in self._workers:
            self._queue.put(_SENTINEL)
        for worker in self._workers:
            worker.join(timeout=2)
        self._workers = []

    def wait_for_idle(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + max(timeout, 0.0)
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    # ------------------------------------------------------------------
    # Public configuration API
    # ------------------------------------------------------------------

    def list_destinations(self) -> list[dict]:
        return [self._serialise_destination(dest) for dest in self._destinations()]

    def create_destination(
        self,
        payload: dict,
        *,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[dict | None, str, int]:
        clean, error = self._clean_destination_payload(payload, existing=None)
        if error:
            return None, error, 400

        destination = WebhookDestination(
            id=f"wh-{uuid.uuid4().hex[:12]}",
            url=clean["url"],
            auth_type=clean["auth_type"],
            secret=clean["secret"],
            custom_headers=clean["custom_headers"],
            event_classes=tuple(clean["event_classes"]),
            enabled=clean["enabled"],
            created_at=_now_z(),
        )

        with self._settings_lock:
            settings = self._store.get_settings()
            settings.webhook_destinations.append(destination)
            self._store.save_settings(settings)

        self._audit_config_event(
            "WEBHOOK_REGISTERED",
            destination,
            requesting_user,
            requesting_ip,
        )
        return self._serialise_destination(destination), "", 201

    def update_destination(
        self,
        destination_id: str,
        payload: dict,
        *,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[dict | None, str, int]:
        existing = self._get_destination(destination_id)
        if existing is None:
            return None, "Webhook not found", 404

        clean, error = self._clean_destination_payload(payload, existing=existing)
        if error:
            return None, error, 400

        def _apply(settings):
            for index, current in enumerate(settings.webhook_destinations):
                if current.id != destination_id:
                    continue
                settings.webhook_destinations[index] = WebhookDestination(
                    id=current.id,
                    url=clean["url"],
                    auth_type=clean["auth_type"],
                    secret=clean["secret"],
                    custom_headers=clean["custom_headers"],
                    event_classes=tuple(clean["event_classes"]),
                    enabled=clean["enabled"],
                    created_at=current.created_at or _now_z(),
                    last_delivery_at=current.last_delivery_at,
                    consecutive_failures=current.consecutive_failures,
                    degraded=current.degraded,
                )
                return settings.webhook_destinations[index]
            return None

        with self._settings_lock:
            settings = self._store.get_settings()
            updated = _apply(settings)
            if updated is None:
                return None, "Webhook not found", 404
            self._store.save_settings(settings)

        self._audit_config_event(
            "WEBHOOK_UPDATED",
            updated,
            requesting_user,
            requesting_ip,
        )
        return self._serialise_destination(updated), "", 200

    def delete_destination(
        self,
        destination_id: str,
        *,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        with self._settings_lock:
            settings = self._store.get_settings()
            original = list(settings.webhook_destinations)
            keep = [dest for dest in original if dest.id != destination_id]
            if len(keep) == len(original):
                return "Webhook not found", 404
            removed = next(dest for dest in original if dest.id == destination_id)
            settings.webhook_destinations = keep
            self._store.save_settings(settings)

        self._audit_config_event(
            "WEBHOOK_DELETED",
            removed,
            requesting_user,
            requesting_ip,
        )
        return "Webhook deleted", 200

    def set_enabled(
        self,
        destination_id: str,
        enabled: bool,
        *,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[dict | None, str, int]:
        if not isinstance(enabled, bool):
            return None, "enabled must be a boolean", 400

        with self._settings_lock:
            settings = self._store.get_settings()
            updated = None
            for index, current in enumerate(settings.webhook_destinations):
                if current.id != destination_id:
                    continue
                updated = WebhookDestination(
                    id=current.id,
                    url=current.url,
                    auth_type=current.auth_type,
                    secret=current.secret,
                    custom_headers=current.custom_headers,
                    event_classes=current.event_classes,
                    enabled=enabled,
                    created_at=current.created_at,
                    last_delivery_at=current.last_delivery_at,
                    consecutive_failures=current.consecutive_failures,
                    degraded=current.degraded,
                )
                settings.webhook_destinations[index] = updated
                break
            if updated is None:
                return None, "Webhook not found", 404
            self._store.save_settings(settings)

        self._audit_config_event(
            "WEBHOOK_ENABLED" if enabled else "WEBHOOK_DISABLED",
            updated,
            requesting_user,
            requesting_ip,
        )
        return self._serialise_destination(updated), "", 200

    def send_test(
        self,
        destination_id: str,
        *,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[dict | None, str, int]:
        destination = self._get_destination(destination_id)
        if destination is None:
            return None, "Webhook not found", 404

        payload = self._build_payload(
            event_type="test",
            timestamp=_now_z(),
            severity="info",
            message="Synthetic webhook test event",
            metadata={
                "triggered_by": requesting_user or "admin",
                "source": "settings-test",
            },
        )
        with self._destination_lock(destination_id):
            result = self._deliver(
                destination=destination,
                event_class="test",
                payload=payload,
                retry_delays=(),
            )
        return result.as_history_entry(), "", 200 if result.delivered else 502

    def list_recent_deliveries(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit or 20), 100))
        retention_days = (
            self._store.get_settings().webhook_delivery_history_retention_days
        )
        cutoff = datetime.now(UTC) - timedelta(days=max(1, retention_days))
        out: list[dict] = []
        for event in self._audit.get_events(limit=MAX_HISTORY_SCAN):
            if event.get("event") not in DELIVERY_AUDIT_EVENTS:
                continue
            if event.get("event") == "WEBHOOK_DELIVERY_DEGRADED":
                continue
            event_time = _parse_iso(event.get("timestamp", ""))
            if event_time is not None and event_time < cutoff:
                continue
            detail = _loads_detail(event.get("detail", ""))
            if not detail:
                continue
            detail["timestamp"] = event.get("timestamp", "")
            detail["audit_event"] = event.get("event", "")
            out.append(detail)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------
    # Event ingestion
    # ------------------------------------------------------------------

    def enqueue_motion_event(self, event_id: str) -> int:
        event = self._motion.get(event_id)
        if event is None or not getattr(event, "ended_at", None):
            return 0
        peak_score = float(getattr(event, "peak_score", 0.0) or 0.0)
        if peak_score < MOTION_NOTIFICATION_THRESHOLD:
            return 0

        camera = self._store.get_camera(getattr(event, "camera_id", "") or "")
        payload = self._build_payload(
            event_type="motion",
            timestamp=getattr(event, "started_at", "") or _now_z(),
            severity="warning" if peak_score >= 0.10 else "info",
            event_id=getattr(event, "id", "") or "",
            camera_id=getattr(event, "camera_id", "") or "",
            camera_name=(getattr(camera, "name", "") if camera else "") or "",
            message=f"Motion detected on {getattr(camera, 'name', '') or getattr(event, 'camera_id', '')}",
            snapshot_url=_snapshot_url_for_event(event),
            metadata={
                "duration_seconds": getattr(event, "duration_seconds", 0.0) or 0.0,
                "peak_score": peak_score,
                "peak_pixels_changed": (getattr(event, "peak_pixels_changed", 0) or 0),
                "deep_link": f"/events/{getattr(event, 'id', '')}",
            },
        )
        return self._enqueue("motion", payload)

    def handle_audit_entry(self, entry: dict) -> None:
        code = str(entry.get("event") or "")
        if code.startswith("WEBHOOK_"):
            return

        payload = None
        event_class = ""
        if code == "CAMERA_OFFLINE":
            camera_id = _extract_camera_id(str(entry.get("detail") or ""))
            camera = self._store.get_camera(camera_id) if camera_id else None
            payload = self._build_payload(
                event_type="camera_offline",
                timestamp=str(entry.get("timestamp") or _now_z()),
                severity="warning",
                camera_id=camera_id,
                camera_name=(getattr(camera, "name", "") if camera else "") or "",
                message=f"Camera went offline: {camera_id or 'unknown camera'}",
                metadata={
                    "audit_event": code,
                    "detail": str(entry.get("detail") or ""),
                },
            )
            event_class = "camera_offline"
        elif code in {"STORAGE_LOW", "RETENTION_RISK"}:
            payload = self._build_payload(
                event_type="storage_low",
                timestamp=str(entry.get("timestamp") or _now_z()),
                severity="error" if code == "RETENTION_RISK" else "warning",
                message="Recordings storage needs attention",
                metadata={
                    "audit_event": code,
                    "detail": str(entry.get("detail") or ""),
                },
            )
            event_class = "storage_low"
        elif code in OTA_OUTCOME_EVENTS:
            severity, outcome = OTA_OUTCOME_EVENTS[code]
            payload = self._build_payload(
                event_type="ota_outcome",
                timestamp=str(entry.get("timestamp") or _now_z()),
                severity=severity,
                message=f"OTA outcome: {outcome}",
                metadata={
                    "audit_event": code,
                    "outcome": outcome,
                    "detail": str(entry.get("detail") or ""),
                },
            )
            event_class = "ota_outcome"

        if payload is not None and event_class:
            self._enqueue(event_class, payload)

    # ------------------------------------------------------------------
    # Delivery workers
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task is _SENTINEL:
                    return
                self._deliver_task(task)
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("webhook delivery worker failed: %s", exc)
            finally:
                self._queue.task_done()

    def _deliver_task(self, task: DeliveryTask) -> None:
        destination = self._get_destination(task.destination_id)
        if destination is None or not destination.enabled:
            return
        if task.event_class not in destination.event_classes:
            return
        with self._destination_lock(task.destination_id):
            destination = self._get_destination(task.destination_id)
            if destination is None or not destination.enabled:
                return
            if task.event_class not in destination.event_classes:
                return
            self._deliver(
                destination=destination,
                event_class=task.event_class,
                payload=task.payload,
                retry_delays=RETRY_DELAYS_SECONDS,
            )

    def _deliver(
        self,
        *,
        destination: WebhookDestination,
        event_class: str,
        payload: dict,
        retry_delays: tuple[int, ...],
    ) -> DeliveryResult:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        headers = self._build_headers(destination, body)
        timestamp = _now_z()

        total_attempts = 1 + len(retry_delays)
        for attempt in range(1, total_attempts + 1):
            started = time.perf_counter()
            try:
                response = self._post_with_redirects(
                    url=destination.url,
                    body=body,
                    headers=headers,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                if 200 <= response.status_code < 300:
                    result = DeliveryResult(
                        destination_id=destination.id,
                        event_type=str(payload.get("event_type") or event_class),
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                        delivered=True,
                        attempt=attempt,
                        will_retry=False,
                        error="",
                        response_excerpt=_excerpt(response.body_text),
                        timestamp=timestamp,
                        url=_safe_url_for_logs(response.final_url),
                    )
                    self._mark_delivery_result(
                        destination.id, success=True, at=timestamp
                    )
                    self._audit_delivery("WEBHOOK_DELIVERY_SUCCESS", result)
                    return result

                error = f"HTTP {response.status_code}"
                will_retry = response.status_code >= 500 and attempt <= len(
                    retry_delays
                )
                result = DeliveryResult(
                    destination_id=destination.id,
                    event_type=str(payload.get("event_type") or event_class),
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    delivered=False,
                    attempt=attempt,
                    will_retry=will_retry,
                    error=error,
                    response_excerpt=_excerpt(response.body_text),
                    timestamp=timestamp,
                    url=_safe_url_for_logs(response.final_url),
                )
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                will_retry = attempt <= len(retry_delays)
                result = DeliveryResult(
                    destination_id=destination.id,
                    event_type=str(payload.get("event_type") or event_class),
                    status_code=None,
                    latency_ms=latency_ms,
                    delivered=False,
                    attempt=attempt,
                    will_retry=will_retry,
                    error=str(exc),
                    response_excerpt="",
                    timestamp=timestamp,
                    url=_safe_url_for_logs(destination.url),
                )

            degraded = self._mark_delivery_result(
                destination.id, success=False, at=timestamp
            )
            self._audit_delivery("WEBHOOK_DELIVERY_FAILED", result)
            if degraded:
                self._audit_degraded_event(destination, payload, timestamp)
            if result.will_retry:
                self._sleep(retry_delays[attempt - 1])
                continue
            return result

        return DeliveryResult(
            destination_id=destination.id,
            event_type=str(payload.get("event_type") or event_class),
            status_code=None,
            latency_ms=0,
            delivered=False,
            attempt=total_attempts,
            will_retry=False,
            error="delivery exhausted retry budget",
            response_excerpt="",
            timestamp=timestamp,
            url=_safe_url_for_logs(destination.url),
        )

    def _post_with_redirects(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
    ) -> HttpResult:
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            self._ensure_public_target(current_url)
            response = self._http_client(
                current_url,
                body,
                headers,
                REQUEST_TIMEOUT_SECONDS,
            )
            if (
                response.status_code not in {301, 302, 303, 307, 308}
                or redirect_count == MAX_REDIRECTS
            ):
                return response
            location = response.headers.get("Location") or response.headers.get(
                "location"
            )
            if not location:
                return response
            current_url = urllib.parse.urljoin(current_url, location)
        return response

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enqueue(self, event_class: str, payload: dict) -> int:
        count = 0
        for destination in self._destinations():
            if not destination.enabled:
                continue
            if event_class != "test" and event_class not in destination.event_classes:
                continue
            self._queue.put(
                DeliveryTask(
                    destination_id=destination.id,
                    event_class=event_class,
                    payload=dict(payload),
                )
            )
            count += 1
        return count

    def _destinations(self) -> list[WebhookDestination]:
        settings = self._store.get_settings()
        return list(getattr(settings, "webhook_destinations", []) or [])

    def _get_destination(self, destination_id: str) -> WebhookDestination | None:
        for destination in self._destinations():
            if destination.id == destination_id:
                return destination
        return None

    def _destination_lock(self, destination_id: str) -> threading.Lock:
        with self._locks_lock:
            return self._destination_locks.setdefault(destination_id, threading.Lock())

    def _serialise_destination(self, destination: WebhookDestination) -> dict:
        return {
            "id": destination.id,
            "url": destination.url,
            "auth_type": destination.auth_type,
            "secret_configured": bool(destination.secret),
            "custom_header_names": sorted(destination.custom_headers.keys()),
            "custom_header_count": len(destination.custom_headers),
            "event_classes": list(destination.event_classes),
            "enabled": destination.enabled,
            "created_at": destination.created_at,
            "last_delivery_at": destination.last_delivery_at,
            "consecutive_failures": destination.consecutive_failures,
            "degraded": destination.degraded,
        }

    def _clean_destination_payload(
        self,
        payload: dict,
        *,
        existing: WebhookDestination | None,
    ) -> tuple[dict, str]:
        if not isinstance(payload, dict):
            return {}, "JSON object required"

        allowed = {
            "url",
            "auth_type",
            "secret",
            "custom_headers",
            "event_classes",
            "enabled",
        }
        unknown = set(payload) - allowed
        if unknown:
            return {}, f"Unknown fields: {', '.join(sorted(unknown))}"

        url = payload.get("url", existing.url if existing else "")
        if not isinstance(url, str) or not url.strip():
            return {}, "url is required"
        url = url.strip()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() != "https":
            return {}, _HTTPS_ONLY_ERROR
        if not parsed.hostname:
            return {}, "Webhook URL must include a host"
        try:
            literal_ip = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal_ip = None
        if literal_ip is not None and _is_private_address(literal_ip):
            return {}, _PRIVATE_TARGET_ERROR

        auth_type = payload.get("auth_type", existing.auth_type if existing else "none")
        if auth_type not in AUTH_TYPE_CHOICES:
            return {}, "auth_type must be one of: none, bearer, hmac"

        if auth_type == "none":
            secret = ""
        else:
            secret = payload.get("secret", existing.secret if existing else "")
            if not isinstance(secret, str) or not secret.strip():
                return {}, "secret is required when auth_type is bearer or hmac"
            secret = secret.strip()

        headers = payload.get(
            "custom_headers",
            dict(existing.custom_headers) if existing else {},
        )
        if headers is None:
            headers = {}
        if not isinstance(headers, dict):
            return {}, "custom_headers must be an object"
        clean_headers: dict[str, str] = {}
        for key, value in headers.items():
            name = str(key).strip()
            if not name:
                return {}, "custom_headers names must be non-empty strings"
            if not _HEADER_NAME_RE.fullmatch(name):
                return {}, "custom_headers names must be valid HTTP header tokens"
            if name.lower() in RESERVED_HEADERS:
                return {}, f"custom_headers cannot override {name}"
            header_value = str(value)
            if "\r" in header_value or "\n" in header_value:
                return {}, "custom_headers values cannot contain newline characters"
            clean_headers[name] = header_value

        raw_event_classes = payload.get(
            "event_classes",
            list(existing.event_classes) if existing else [],
        )
        if isinstance(raw_event_classes, str):
            raw_event_classes = [raw_event_classes]
        if not isinstance(raw_event_classes, (list, tuple, set, frozenset)):
            return {}, "event_classes must be an array"
        clean_event_classes = sorted(
            {str(value).strip() for value in raw_event_classes if str(value).strip()}
        )
        invalid = [
            value for value in clean_event_classes if value not in EVENT_CLASS_CHOICES
        ]
        if invalid:
            return {}, f"Unsupported event_classes: {', '.join(sorted(invalid))}"
        if not clean_event_classes:
            return {}, "At least one event class must be selected"

        enabled = payload.get("enabled", existing.enabled if existing else True)
        if not isinstance(enabled, bool):
            return {}, "enabled must be a boolean"

        return {
            "url": url,
            "auth_type": auth_type,
            "secret": secret,
            "custom_headers": clean_headers,
            "event_classes": clean_event_classes,
            "enabled": enabled,
        }, ""

    def _build_payload(
        self,
        *,
        event_type: str,
        timestamp: str,
        severity: str,
        message: str,
        event_id: str = "",
        camera_id: str = "",
        camera_name: str = "",
        snapshot_url: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        return {
            "schema_version": 1,
            "event_id": event_id or f"evt-{uuid.uuid4().hex[:12]}",
            "event_type": event_type,
            "severity": severity,
            "timestamp": _normalise_iso_z(timestamp),
            "camera_id": camera_id,
            "camera_name": camera_name,
            "message": message,
            "snapshot_url": snapshot_url,
            "metadata": metadata or {},
        }

    def _build_headers(
        self, destination: WebhookDestination, body: bytes
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "home-monitor-webhook/1",
        }
        headers.update(destination.custom_headers)
        if destination.auth_type == "bearer" and destination.secret:
            headers["Authorization"] = f"Bearer {destination.secret}"
        if destination.auth_type == "hmac" and destination.secret:
            digest = hmac.new(
                destination.secret.encode("utf-8"),
                body,
                sha256,
            ).hexdigest()
            headers["X-Webhook-Signature"] = f"sha256={digest}"
        return headers

    def _ensure_public_target(self, url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() != "https":
            raise WebhookConfigError(_HTTPS_ONLY_ERROR)
        host = parsed.hostname or ""
        port = parsed.port or 443
        try:
            ip_literal = ipaddress.ip_address(host)
        except ValueError:
            ip_literal = None

        addresses: list[ipaddress._BaseAddress] = []
        if ip_literal is not None:
            addresses.append(ip_literal)
        else:
            try:
                infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except socket.gaierror:
                return
            for info in infos:
                candidate = info[4][0]
                try:
                    addresses.append(ipaddress.ip_address(candidate))
                except ValueError:
                    continue

        if not addresses:
            return
        if any(_is_private_address(address) for address in addresses):
            raise WebhookConfigError(_PRIVATE_TARGET_ERROR)

    def _mark_delivery_result(
        self, destination_id: str, *, success: bool, at: str
    ) -> bool:
        degraded_now = False
        with self._settings_lock:
            settings = self._store.get_settings()
            for destination in settings.webhook_destinations:
                if destination.id != destination_id:
                    continue
                if success:
                    destination.last_delivery_at = at
                    destination.consecutive_failures = 0
                    destination.degraded = False
                else:
                    destination.consecutive_failures += 1
                    degraded_now = (
                        destination.consecutive_failures >= DEGRADED_FAILURE_THRESHOLD
                        and not destination.degraded
                    )
                    if degraded_now:
                        destination.degraded = True
                self._store.save_settings(settings)
                return degraded_now
        return False

    def _audit_config_event(
        self,
        event: str,
        destination: WebhookDestination,
        user: str,
        ip: str,
    ) -> None:
        if self._audit is None:
            return
        detail = json.dumps(
            {
                "destination_id": destination.id,
                "url": _safe_url_for_logs(destination.url),
                "auth_type": destination.auth_type,
                "event_classes": list(destination.event_classes),
                "enabled": destination.enabled,
                "custom_header_names": sorted(destination.custom_headers.keys()),
            },
            separators=(",", ":"),
        )
        self._audit.log_event(event, user=user, ip=ip, detail=detail)

    def _audit_delivery(self, event: str, result: DeliveryResult) -> None:
        if self._audit is None:
            return
        self._audit.log_event(
            event,
            user="system",
            ip="",
            detail=json.dumps(result.as_history_entry(), separators=(",", ":")),
        )

    def _audit_degraded_event(
        self,
        destination: WebhookDestination,
        payload: dict,
        timestamp: str,
    ) -> None:
        if self._audit is None:
            return
        self._audit.log_event(
            "WEBHOOK_DELIVERY_DEGRADED",
            user="system",
            ip="",
            detail=json.dumps(
                {
                    "destination_id": destination.id,
                    "event_type": payload.get("event_type", ""),
                    "timestamp": timestamp,
                    "url": _safe_url_for_logs(destination.url),
                },
                separators=(",", ":"),
            ),
        )


def _default_http_post(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: int,
) -> HttpResult:
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirectHandler(),
    )
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(request, timeout=timeout) as response:
            body_text = response.read().decode("utf-8", "replace")
            return HttpResult(
                status_code=response.getcode(),
                headers=dict(response.headers.items()),
                body_text=body_text,
                final_url=response.geturl(),
            )
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace")
        return HttpResult(
            status_code=exc.code,
            headers=dict(exc.headers.items()),
            body_text=body_text,
            final_url=exc.geturl(),
        )
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def _snapshot_url_for_event(event) -> str | None:
    clip_ref = getattr(event, "clip_ref", None) or {}
    base = str(clip_ref.get("filename") or "").rsplit(".", 1)[0]
    if not base:
        return None
    camera_id = str(clip_ref.get("camera_id") or "")
    date = str(clip_ref.get("date") or "")
    return f"/api/v1/recordings/{camera_id}/{date}/{base}.jpg"


def _extract_camera_id(detail: str) -> str:
    token = detail.split()[0] if detail else ""
    if token.startswith("cam-") or token.startswith("camera-"):
        return token.rstrip(":,;")
    match = _MOTION_DETAIL_RE.match(detail)
    if match:
        return match.group("camera_id")
    return ""


def _is_private_address(address: ipaddress._BaseAddress) -> bool:
    if getattr(address, "ipv4_mapped", None) is not None:
        address = address.ipv4_mapped
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_iso_z(value: str) -> str:
    if not isinstance(value, str) or not value:
        return _now_z()
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone(UTC)
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    except (TypeError, ValueError):
        return value


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except (AttributeError, TypeError, ValueError):
        return None


def _safe_url_for_logs(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme, host, path, "", ""))


def _loads_detail(detail: str) -> dict:
    if not isinstance(detail, str) or not detail:
        return {}
    try:
        data = json.loads(detail)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _excerpt(text: str) -> str:
    compact = " ".join(str(text or "").split())
    return compact[:MAX_RESPONSE_EXCERPT]
