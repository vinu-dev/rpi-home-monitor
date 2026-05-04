# REQ: SWR-024, SWR-057; RISK: RISK-012, RISK-017, RISK-020; SEC: SC-012, SC-020; TEST: TC-023, TC-041, TC-049
"""Offsite backup service for finalized recordings.

Runs inside the existing server process as a low-priority background worker.
The service scans finalized local clips, persists a retry queue on /data, and
mirrors eligible files to an operator-controlled S3-compatible bucket.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from monitor.models import Settings
from monitor.services.recordings_service import (
    _ACTIVE_WRITE_SECONDS,
    _CAMERA_ID_RE,
    _parse_clip_date_time,
)

log = logging.getLogger("monitor.services.offsite_backup")

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9._/-]{0,256}$")
_ENDPOINT_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(?::\d{1,5})?$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _normalise_prefix(value: str) -> str:
    return value.strip().strip("/")


def _endpoint_url(value: str) -> str:
    cleaned = (value or "").strip()
    if "://" in cleaned:
        return cleaned
    return f"https://{cleaned}"


def _friendly_remote_error(exc: Exception) -> str:
    name = exc.__class__.__name__
    message = str(exc)
    lowered = message.lower()

    if "bucket" in lowered and ("not found" in lowered or "no such bucket" in lowered):
        return "Configured bucket was not found"
    if (
        "access denied" in lowered
        or "accessdenied" in lowered
        or "invalidaccesskeyid" in lowered
        or "signaturedoesnotmatch" in lowered
        or "forbidden" in lowered
        or name in {"NoCredentialsError", "PartialCredentialsError"}
    ):
        return "Remote storage rejected the provided credentials"
    if (
        "timeout" in lowered
        or "could not connect" in lowered
        or "connection" in lowered
        or "endpoint" in lowered
        or name
        in {
            "ConnectTimeoutError",
            "EndpointConnectionError",
            "ReadTimeoutError",
        }
    ):
        return "Could not reach the remote storage endpoint"
    if "boto3 is not installed" in lowered:
        return "Offsite backup runtime dependency is missing on this host"
    return "Remote storage request failed"


class _ThrottledReader:
    """File-like wrapper that enforces an approximate bandwidth ceiling."""

    def __init__(self, handle, bytes_per_second: float | None):
        self._handle = handle
        self._bytes_per_second = bytes_per_second
        self._started_at = time.monotonic()
        self._bytes_sent = 0

    def read(self, size: int = -1):
        chunk = self._handle.read(size)
        if chunk and self._bytes_per_second:
            self._bytes_sent += len(chunk)
            expected_seconds = self._bytes_sent / self._bytes_per_second
            elapsed_seconds = time.monotonic() - self._started_at
            delay = expected_seconds - elapsed_seconds
            if delay > 0:
                time.sleep(delay)
        return chunk


@dataclass(frozen=True)
class RemoteObject:
    key: str
    last_modified: datetime


class Boto3S3Client:
    """Thin boto3 adapter used by OffsiteBackupService."""

    def __init__(self, endpoint: str, access_key_id: str, secret_access_key: str):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on host env
            raise RuntimeError("boto3 is not installed") from exc

        self._client = boto3.client(
            "s3",
            endpoint_url=_endpoint_url(endpoint),
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="us-east-1",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def upload_file(
        self,
        bucket: str,
        key: str,
        source_path: str,
        bandwidth_cap_mbps: float | None = None,
    ) -> None:
        bytes_per_second = None
        if bandwidth_cap_mbps:
            bytes_per_second = float(bandwidth_cap_mbps) * 1024 * 1024

        with Path(source_path).open("rb") as handle:
            body = _ThrottledReader(handle, bytes_per_second)
            self._client.put_object(Bucket=bucket, Key=key, Body=body)

    def write_probe(self, bucket: str, key: str) -> None:
        self._client.put_object(Bucket=bucket, Key=key, Body=b"home-monitor-probe")

    def delete_object(self, bucket: str, key: str) -> None:
        self._client.delete_object(Bucket=bucket, Key=key)

    def iter_objects(self, bucket: str, prefix: str) -> list[RemoteObject]:
        paginator = self._client.get_paginator("list_objects_v2")
        objects: list[RemoteObject] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item.get("Key", "")
                last_modified = item.get("LastModified")
                if not key or last_modified is None:
                    continue
                if isinstance(last_modified, datetime):
                    objects.append(
                        RemoteObject(
                            key=key,
                            last_modified=last_modified.astimezone(UTC),
                        )
                    )
        return objects


class OffsiteBackupService:
    """Scans finalized clips and mirrors them to S3-compatible storage."""

    LOOP_INTERVAL_SECONDS = 30
    MAX_PENDING_ITEMS = 100
    MAX_RETRIES = 5
    MAX_UPLOADS_PER_CYCLE = 3
    MAX_FAILED_ITEMS = 100
    RETENTION_SWEEP_SECONDS = 24 * 60 * 60

    def __init__(
        self,
        store,
        audit,
        config_dir: str,
        recordings_dir: str,
        client_factory=None,
        interval_seconds: int | None = None,
    ):
        self._store = store
        self._audit = audit
        self._recordings_dir = recordings_dir
        self._state_path = Path(config_dir) / "offsite_backup_queue.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._client_factory = client_factory or self._make_client
        self._interval_seconds = interval_seconds or self.LOOP_INTERVAL_SECONDS
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="offsite-backup",
            daemon=True,
        )
        self._thread.start()
        log.info("Offsite backup worker started")

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def request_sync(self) -> None:
        self._wake_event.set()

    def set_recordings_dir(self, recordings_dir: str) -> None:
        self._recordings_dir = recordings_dir

    def run_once(self) -> None:
        settings = self._store.get_settings()
        state = self._load_state()
        discovered = self._discover_finalized_clips(settings)
        discovered_ids = set(discovered)

        self._drop_missing_entries(state, discovered_ids)
        self._prune_uploaded_index(state, discovered_ids)
        self._enqueue_new_clips(state, discovered)

        if settings.offsite_backup_enabled and self._is_fully_configured(settings):
            self._process_pending_uploads(state, settings)
            self._run_retention_cleanup(state, settings)

        self._save_state(state)

    def get_settings_status(self) -> dict:
        settings = self._store.get_settings()
        state = self._load_state()
        next_retry_at = self._next_retry_at(state)
        return {
            "enabled": bool(settings.offsite_backup_enabled),
            "configured": self._is_fully_configured(settings),
            "secret_configured": bool(settings.offsite_backup_secret_access_key),
            "endpoint": settings.offsite_backup_endpoint,
            "bucket": settings.offsite_backup_bucket,
            "access_key_id": settings.offsite_backup_access_key_id,
            "prefix": settings.offsite_backup_prefix,
            "retention_days": settings.offsite_backup_retention_days,
            "bandwidth_cap_mbps": settings.offsite_backup_bandwidth_cap_mbps,
            "queue_size": len(state["pending"]),
            "queue_limit": self.MAX_PENDING_ITEMS,
            "failed_count": len(state["failed"]),
            "last_success_at": state["last_success_at"],
            "next_retry_at": next_retry_at,
            "last_error": state["last_error"],
            "last_error_at": state["last_error_at"],
        }

    def update_config(
        self,
        payload: dict,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        if not isinstance(payload, dict) or not payload:
            return "No offsite backup fields provided", 400

        cleaned, error = self._clean_payload(payload)
        if error:
            return error, 400

        settings = self._store.get_settings()
        merged = self._merged_config(settings, cleaned)
        validation_error = self._validate_config(
            merged, require_complete=bool(merged["enabled"])
        )
        if validation_error:
            return validation_error, 400

        changed_fields = self._apply_config(settings, merged)
        self._store.save_settings(settings)
        if changed_fields:
            self._reset_retry_state()
            self._log_audit(
                "BACKUP_CREDENTIALS_UPDATED",
                requesting_user,
                requesting_ip,
                f"updated offsite backup fields: {', '.join(sorted(changed_fields))}",
            )
            self.request_sync()
        return "Offsite backup settings updated", 200

    def test_connection(
        self,
        payload: dict | None,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        cleaned, error = self._clean_payload(payload or {})
        if error:
            return error, 400

        settings = self._store.get_settings()
        merged = self._merged_config(settings, cleaned)
        validation_error = self._validate_config(merged, require_complete=True)
        if validation_error:
            return validation_error, 400

        probe_key = self._build_object_key(
            merged["prefix"],
            "__home-monitor-probes__",
            f"{int(time.time())}.txt",
        )
        self._log_audit(
            "BACKUP_STARTED",
            requesting_user,
            requesting_ip,
            "connection probe",
        )
        try:
            client = self._client_factory(merged)
            client.write_probe(merged["bucket"], probe_key)
            client.delete_object(merged["bucket"], probe_key)
        except Exception as exc:
            friendly = _friendly_remote_error(exc)
            log.warning(
                "Offsite backup connection probe failed: %s", exc.__class__.__name__
            )
            self._log_audit(
                "BACKUP_FAILED",
                requesting_user,
                requesting_ip,
                f"connection probe failed: {friendly}",
            )
            return friendly, 502

        self._log_audit(
            "BACKUP_SUCCESS",
            requesting_user,
            requesting_ip,
            "connection probe succeeded",
        )
        return "Connection OK", 200

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:  # pragma: no cover - defensive
                log.warning("Offsite backup cycle failed: %s", exc)
            self._wake_event.wait(self._interval_seconds)
            self._wake_event.clear()

    def _make_client(self, config: dict) -> Boto3S3Client:
        return Boto3S3Client(
            endpoint=config["endpoint"],
            access_key_id=config["access_key_id"],
            secret_access_key=config["secret_access_key"],
        )

    def _state_template(self) -> dict:
        return {
            "pending": [],
            "uploaded": {},
            "failed": [],
            "last_success_at": "",
            "last_error": "",
            "last_error_at": "",
            "last_retention_cleanup_at": "",
        }

    def _load_state(self) -> dict:
        state = self._state_template()
        if not self._state_path.exists():
            return state
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return state
        if not isinstance(raw, dict):
            return state

        pending = raw.get("pending", [])
        uploaded = raw.get("uploaded", {})
        failed = raw.get("failed", [])
        state["pending"] = [item for item in pending if isinstance(item, dict)]
        state["uploaded"] = uploaded if isinstance(uploaded, dict) else {}
        state["failed"] = [item for item in failed if isinstance(item, dict)]
        for key in (
            "last_success_at",
            "last_error",
            "last_error_at",
            "last_retention_cleanup_at",
        ):
            value = raw.get(key, "")
            state[key] = value if isinstance(value, str) else ""
        return state

    def _save_state(self, state: dict) -> None:
        tmp_path = self._state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp_path.replace(self._state_path)

    def _discover_finalized_clips(self, settings: Settings) -> dict[str, dict]:
        root = Path(self._recordings_dir)
        if not root.is_dir():
            return {}

        prefix = settings.offsite_backup_prefix
        now = time.time()
        discovered: dict[str, dict] = {}

        try:
            children = sorted(root.iterdir())
        except OSError:
            return {}

        for cam_dir in children:
            if not cam_dir.is_dir() or not _CAMERA_ID_RE.match(cam_dir.name):
                continue
            for mp4 in cam_dir.rglob("*.mp4"):
                try:
                    stat = mp4.stat()
                except OSError:
                    continue
                if now - stat.st_mtime < _ACTIVE_WRITE_SECONDS:
                    continue
                clip_date, _start_time = _parse_clip_date_time(mp4)
                if not clip_date:
                    continue
                clip_id = f"{cam_dir.name}/{clip_date}/{mp4.name}"
                discovered[clip_id] = {
                    "clip_id": clip_id,
                    "camera_id": cam_dir.name,
                    "date": clip_date,
                    "filename": mp4.name,
                    "path": str(mp4),
                    "size_bytes": stat.st_size,
                    "object_key": self._build_object_key(
                        prefix,
                        cam_dir.name,
                        clip_date,
                        mp4.name,
                    ),
                    "enqueued_at": _iso_now(),
                    "attempts": 0,
                    "next_attempt_at": "",
                    "last_error": "",
                }
        return discovered

    def _drop_missing_entries(self, state: dict, discovered_ids: set[str]) -> None:
        pending: list[dict] = []
        for item in state["pending"]:
            clip_id = item.get("clip_id", "")
            path = item.get("path", "")
            if clip_id in discovered_ids or (path and Path(path).exists()):
                pending.append(item)
                continue
            self._mark_error(
                state,
                f"Local clip disappeared before backup: {clip_id}",
            )
            self._log_audit(
                "BACKUP_FAILED",
                "",
                "",
                f"local clip vanished before upload: {clip_id}",
            )
        state["pending"] = pending

        failed: list[dict] = []
        for item in state["failed"]:
            clip_id = item.get("clip_id", "")
            path = item.get("path", "")
            if clip_id in discovered_ids or (path and Path(path).exists()):
                failed.append(item)
        state["failed"] = failed[-self.MAX_FAILED_ITEMS :]

    def _prune_uploaded_index(self, state: dict, discovered_ids: set[str]) -> None:
        state["uploaded"] = {
            clip_id: uploaded_at
            for clip_id, uploaded_at in state["uploaded"].items()
            if clip_id in discovered_ids
        }

    def _enqueue_new_clips(self, state: dict, discovered: dict[str, dict]) -> None:
        queued_ids = {item.get("clip_id", "") for item in state["pending"]}
        failed_ids = {item.get("clip_id", "") for item in state["failed"]}
        uploaded_ids = set(state["uploaded"])

        new_items = [
            discovered[clip_id]
            for clip_id in sorted(discovered)
            if clip_id not in queued_ids
            and clip_id not in failed_ids
            and clip_id not in uploaded_ids
        ]
        if not new_items:
            return

        state["pending"].extend(new_items)
        overflow = len(state["pending"]) - self.MAX_PENDING_ITEMS
        if overflow <= 0:
            return

        dropped = state["pending"][:overflow]
        state["pending"] = state["pending"][overflow:]
        if dropped:
            self._mark_error(
                state,
                f"Queue cap reached; dropped {len(dropped)} pending clip(s)",
            )
            self._log_audit(
                "BACKUP_FAILED",
                "",
                "",
                f"queue cap reached; dropped {len(dropped)} pending clip(s)",
            )

    def _process_pending_uploads(self, state: dict, settings: Settings) -> None:
        uploads_this_cycle = 0
        now = _utc_now()
        for item in list(state["pending"]):
            if uploads_this_cycle >= self.MAX_UPLOADS_PER_CYCLE:
                return
            next_attempt = _parse_iso(item.get("next_attempt_at", ""))
            if next_attempt and next_attempt > now:
                continue

            uploads_this_cycle += 1
            clip_id = item.get("clip_id", "")
            path = item.get("path", "")
            if not path or not Path(path).is_file():
                state["pending"].remove(item)
                self._mark_error(state, f"Local clip unavailable: {clip_id}")
                self._log_audit(
                    "BACKUP_FAILED",
                    "",
                    "",
                    f"local clip missing during upload: {clip_id}",
                )
                continue

            self._log_audit("BACKUP_STARTED", "", "", f"uploading {clip_id}")
            try:
                client = self._client_factory(self._settings_dict(settings))
                client.upload_file(
                    settings.offsite_backup_bucket,
                    item["object_key"],
                    path,
                    settings.offsite_backup_bandwidth_cap_mbps,
                )
            except Exception as exc:
                friendly = _friendly_remote_error(exc)
                log.warning(
                    "Offsite backup upload failed for %s: %s",
                    clip_id,
                    exc.__class__.__name__,
                )
                self._handle_upload_failure(state, item, friendly)
                continue

            state["pending"].remove(item)
            state["uploaded"][clip_id] = _iso_now()
            state["last_success_at"] = _iso_now()
            state["last_error"] = ""
            state["last_error_at"] = ""
            self._log_audit("BACKUP_SUCCESS", "", "", f"uploaded {clip_id}")

    def _handle_upload_failure(self, state: dict, item: dict, friendly: str) -> None:
        clip_id = item.get("clip_id", "")
        attempts = int(item.get("attempts", 0)) + 1
        item["attempts"] = attempts
        item["last_error"] = friendly

        if attempts >= self.MAX_RETRIES:
            state["pending"].remove(item)
            item["failed_at"] = _iso_now()
            state["failed"].append(item)
            state["failed"] = state["failed"][-self.MAX_FAILED_ITEMS :]
            self._mark_error(
                state,
                f"Permanent backup failure for {clip_id}: {friendly}",
            )
            self._log_audit(
                "BACKUP_FAILED",
                "",
                "",
                f"permanent upload failure for {clip_id}: {friendly}",
            )
            return

        delay_seconds = min(2 ** (attempts - 1), 300)
        retry_at = _utc_now() + timedelta(seconds=delay_seconds)
        item["next_attempt_at"] = _to_iso(retry_at)
        self._mark_error(
            state,
            f"Retrying {clip_id} in {delay_seconds}s: {friendly}",
        )
        self._log_audit(
            "BACKUP_FAILED",
            "",
            "",
            f"upload failed for {clip_id}; retrying in {delay_seconds}s",
        )

    def _run_retention_cleanup(self, state: dict, settings: Settings) -> None:
        retention_days = settings.offsite_backup_retention_days
        if retention_days is None:
            return

        last_cleanup = _parse_iso(state["last_retention_cleanup_at"])
        if (
            last_cleanup
            and (_utc_now() - last_cleanup).total_seconds()
            < self.RETENTION_SWEEP_SECONDS
        ):
            return

        cutoff = _utc_now() - timedelta(days=retention_days)
        prefix = _normalise_prefix(settings.offsite_backup_prefix)
        list_prefix = f"{prefix}/" if prefix else ""

        try:
            client = self._client_factory(self._settings_dict(settings))
            objects = client.iter_objects(settings.offsite_backup_bucket, list_prefix)
            deleted = 0
            for obj in objects:
                if obj.last_modified >= cutoff:
                    continue
                client.delete_object(settings.offsite_backup_bucket, obj.key)
                deleted += 1
            if deleted:
                self._log_audit(
                    "BACKUP_RETENTION_DELETED",
                    "",
                    "",
                    f"deleted {deleted} remote object(s) older than {retention_days} day(s)",
                )
            state["last_retention_cleanup_at"] = _iso_now()
        except Exception as exc:
            friendly = _friendly_remote_error(exc)
            log.warning("Offsite retention cleanup failed: %s", exc.__class__.__name__)
            self._mark_error(state, f"Retention cleanup failed: {friendly}")
            self._log_audit(
                "BACKUP_FAILED",
                "",
                "",
                f"retention cleanup failed: {friendly}",
            )

    def _build_object_key(self, prefix: str, *parts: str) -> str:
        cleaned_parts = [part.strip("/") for part in parts if part and part.strip("/")]
        if prefix:
            return "/".join([_normalise_prefix(prefix), *cleaned_parts])
        return "/".join(cleaned_parts)

    def _clean_payload(self, payload: dict) -> tuple[dict, str]:
        if not isinstance(payload, dict):
            return {}, "JSON body must be an object"

        allowed = {
            "enabled",
            "endpoint",
            "bucket",
            "access_key_id",
            "secret_access_key",
            "prefix",
            "retention_days",
            "bandwidth_cap_mbps",
        }
        unknown = set(payload) - allowed
        if unknown:
            return {}, f"Unknown fields: {', '.join(sorted(unknown))}"

        cleaned: dict = {}

        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                return {}, "enabled must be a boolean"
            cleaned["enabled"] = payload["enabled"]

        for field in (
            "endpoint",
            "bucket",
            "access_key_id",
            "secret_access_key",
            "prefix",
        ):
            if field not in payload:
                continue
            value = payload[field]
            if not isinstance(value, str):
                return {}, f"{field} must be a string"
            cleaned[field] = value.strip()

        if "retention_days" in payload:
            value = payload["retention_days"]
            if value in ("", None):
                cleaned["retention_days"] = None
            elif isinstance(value, bool) or not isinstance(value, int):
                return {}, "retention_days must be an integer or null"
            else:
                cleaned["retention_days"] = value

        if "bandwidth_cap_mbps" in payload:
            value = payload["bandwidth_cap_mbps"]
            if value in ("", None):
                cleaned["bandwidth_cap_mbps"] = None
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                return {}, "bandwidth_cap_mbps must be a number or null"
            else:
                cleaned["bandwidth_cap_mbps"] = float(value)

        if "prefix" in cleaned:
            cleaned["prefix"] = _normalise_prefix(cleaned["prefix"])

        return cleaned, ""

    def _merged_config(self, settings: Settings, payload: dict) -> dict:
        current = self._settings_dict(settings)
        merged = dict(current)
        merged.update(payload)
        if "secret_access_key" in payload and payload["secret_access_key"] == "":
            merged["secret_access_key"] = current["secret_access_key"]
        return merged

    def _settings_dict(self, settings: Settings) -> dict:
        return {
            "enabled": bool(settings.offsite_backup_enabled),
            "endpoint": settings.offsite_backup_endpoint,
            "bucket": settings.offsite_backup_bucket,
            "access_key_id": settings.offsite_backup_access_key_id,
            "secret_access_key": settings.offsite_backup_secret_access_key,
            "prefix": settings.offsite_backup_prefix,
            "retention_days": settings.offsite_backup_retention_days,
            "bandwidth_cap_mbps": settings.offsite_backup_bandwidth_cap_mbps,
        }

    def _apply_config(self, settings: Settings, merged: dict) -> list[str]:
        field_map = {
            "enabled": "offsite_backup_enabled",
            "endpoint": "offsite_backup_endpoint",
            "bucket": "offsite_backup_bucket",
            "access_key_id": "offsite_backup_access_key_id",
            "secret_access_key": "offsite_backup_secret_access_key",
            "prefix": "offsite_backup_prefix",
            "retention_days": "offsite_backup_retention_days",
            "bandwidth_cap_mbps": "offsite_backup_bandwidth_cap_mbps",
        }

        changed: list[str] = []
        for public_name, attr_name in field_map.items():
            current_value = getattr(settings, attr_name)
            new_value = merged[public_name]
            if current_value == new_value:
                continue
            setattr(settings, attr_name, new_value)
            changed.append(public_name)
        return changed

    def _validate_config(self, config: dict, require_complete: bool) -> str:
        endpoint = config["endpoint"]
        bucket = config["bucket"]
        access_key_id = config["access_key_id"]
        secret_access_key = config["secret_access_key"]
        prefix = config["prefix"]
        retention_days = config["retention_days"]
        bandwidth_cap_mbps = config["bandwidth_cap_mbps"]

        if require_complete:
            missing = [
                name
                for name, value in (
                    ("endpoint", endpoint),
                    ("bucket", bucket),
                    ("access_key_id", access_key_id),
                    ("secret_access_key", secret_access_key),
                )
                if not value
            ]
            if missing:
                return f"Missing required offsite backup fields: {', '.join(missing)}"

        if endpoint:
            parsed = urlparse(_endpoint_url(endpoint))
            if parsed.scheme != "https":
                return "endpoint must use HTTPS"
            if not parsed.netloc or not _ENDPOINT_HOST_RE.match(parsed.netloc):
                return "endpoint must be a host or host:port"
            if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                return "endpoint must not include a path, query, or fragment"

        if bucket:
            if not _BUCKET_RE.match(bucket):
                return "bucket must be a valid S3 bucket name"
            if ".." in bucket or ".-" in bucket or "-." in bucket:
                return "bucket must be a valid S3 bucket name"
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", bucket):
                return "bucket must not look like an IP address"

        if access_key_id and len(access_key_id) > 256:
            return "access_key_id must be at most 256 characters"

        if secret_access_key and len(secret_access_key) > 256:
            return "secret_access_key must be at most 256 characters"

        if prefix and not _PREFIX_RE.match(prefix):
            return "prefix may only contain letters, numbers, ., _, -, and /"

        if retention_days is not None and (retention_days < 1 or retention_days > 3650):
            return "retention_days must be between 1 and 3650 or null"

        if bandwidth_cap_mbps is not None and (
            bandwidth_cap_mbps <= 0 or bandwidth_cap_mbps > 1000
        ):
            return "bandwidth_cap_mbps must be between 0 and 1000 or null"

        return ""

    def _is_fully_configured(self, settings: Settings) -> bool:
        return all(
            (
                settings.offsite_backup_endpoint,
                settings.offsite_backup_bucket,
                settings.offsite_backup_access_key_id,
                settings.offsite_backup_secret_access_key,
            )
        )

    def _reset_retry_state(self) -> None:
        state = self._load_state()
        for item in state["pending"]:
            item["attempts"] = 0
            item["next_attempt_at"] = ""
            item["last_error"] = ""
        for item in state["failed"]:
            item["attempts"] = 0
            item["next_attempt_at"] = ""
            item["last_error"] = ""
            state["pending"].append(item)
        state["failed"] = []
        state["last_error"] = ""
        state["last_error_at"] = ""
        self._save_state(state)

    def _mark_error(self, state: dict, message: str) -> None:
        state["last_error"] = message
        state["last_error_at"] = _iso_now()

    def _next_retry_at(self, state: dict) -> str:
        retry_times = []
        for item in state["pending"]:
            value = _parse_iso(item.get("next_attempt_at", ""))
            if value is not None:
                retry_times.append(value)
        if not retry_times:
            return ""
        return _to_iso(min(retry_times))

    def _log_audit(self, event: str, user: str, ip: str, detail: str) -> None:
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception:  # pragma: no cover - fail-silent audit
            log.debug("Audit log failed for %s", event)
