# REQ: SWR-023, SWR-024, SWR-034, SWR-045; RISK: RISK-011, RISK-012, RISK-019, RISK-020; SEC: SC-011, SC-012, SC-017, SC-020, SC-021; TEST: TC-022, TC-023, TC-032, TC-041, TC-042
"""
Configuration backup/export service.

This service serialises the server's mutable configuration into a signed
bundle, previews the bundle before restore, and restores selected
configuration components with rollback-on-error semantics.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import shutil
import tempfile
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from monitor.models import Camera, Settings, User
from monitor.services.backup_paths import build_backup_paths

log = logging.getLogger("monitor.services.config_backup")

BUNDLE_FORMAT = "home-monitor-config-backup.v1"
BUNDLE_SCHEMA_VERSION = 1
PBKDF2_ITERATIONS = 200_000
MIN_PASSPHRASE_LENGTH = 12
DEFAULT_SNAPSHOT_HISTORY = 3


class ConfigBackupError(ValueError):
    """Raised when a bundle cannot be exported, previewed, or imported."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "invalid_bundle",
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


class ConfigBackupService:
    """Export, preview, and restore configuration bundles."""

    def __init__(
        self,
        store,
        audit=None,
        settings_service=None,
        data_dir: str = "/data",
        config_dir: str | None = None,
        certs_dir: str | None = None,
    ):
        self._store = store
        self._audit = audit
        self._settings_service = settings_service
        self._paths = build_backup_paths(
            data_dir=data_dir,
            config_dir=config_dir,
            certs_dir=certs_dir,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def export_bundle(
        self,
        *,
        passphrase: str,
        options: dict | None = None,
    ) -> tuple[str, bytes, dict]:
        """Build a downloadable bundle and a human-readable preview."""
        options = self._normalise_export_options(options)
        payload = self._build_payload(options)
        manifest = self._build_manifest(payload, options)
        signature = self._sign_manifest_and_payload(
            manifest=manifest,
            payload=payload,
            passphrase=passphrase,
        )
        bundle = {
            "manifest": manifest,
            "payload": payload,
            "signature": signature,
        }
        created_at = manifest["created_at"].replace(":", "").replace("-", "")
        filename = f"home-monitor-config-{created_at}.hmb"
        preview = self._build_preview(manifest, payload)
        return filename, self._canonical_json(bundle), preview

    def preview_bundle(self, bundle_bytes: bytes, *, passphrase: str) -> dict:
        """Validate and summarise a bundle before restore."""
        bundle = self._load_bundle(bundle_bytes, passphrase=passphrase)
        return self._build_preview(bundle["manifest"], bundle["payload"])

    def import_bundle(
        self,
        bundle_bytes: bytes,
        *,
        passphrase: str,
        restore_options: dict | None = None,
    ) -> dict:
        """Restore a validated bundle into the live data directory."""
        bundle = self._load_bundle(bundle_bytes, passphrase=passphrase)
        restore = self._normalise_restore_options(
            restore_options,
            manifest=bundle["manifest"],
        )
        payload = self._normalise_payload(bundle["payload"], restore=restore)
        snapshot = self._create_snapshot(
            restore=restore,
            manifest=bundle["manifest"],
            payload=payload,
        )
        try:
            self._apply_import(payload=payload, restore=restore)
        except Exception as exc:  # pragma: no cover - defensive rollback path
            self._restore_snapshot(snapshot)
            raise ConfigBackupError(
                "Restore failed and prior state was restored",
                reason="import_failed",
                status_code=500,
            ) from exc
        self._prune_snapshots()
        return {
            "message": "Configuration restored",
            "snapshot": snapshot["metadata"],
            "preview": self._build_preview(bundle["manifest"], bundle["payload"]),
            "restored_components": sorted(
                name for name, enabled in restore.items() if enabled
            ),
        }

    def list_snapshots(self) -> list[dict]:
        """Return stored rollback snapshots, newest first."""
        result: list[dict] = []
        root = self._paths.backup_snapshot_root
        if not root.is_dir():
            return result

        for entry in sorted(root.iterdir(), reverse=True):
            metadata_file = entry / "metadata.json"
            if not metadata_file.is_file():
                continue
            try:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            result.append(metadata)
        return result

    # ------------------------------------------------------------------
    # Bundle construction
    # ------------------------------------------------------------------
    def _build_payload(self, options: dict) -> dict:
        settings = self._store.get_settings()
        payload = {
            "users": [],
            "cameras": [],
            "settings": None,
            "camera_trust": {"entries": []},
        }

        if options["users"]:
            payload["users"] = [
                self._serialise_user(
                    user,
                    include_credentials=options["include_user_credentials"],
                )
                for user in self._store.get_users()
            ]

        if options["cameras"]:
            payload["cameras"] = [
                self._serialise_camera(
                    camera,
                    include_camera_trust=options["include_camera_trust"],
                )
                for camera in self._store.get_cameras()
            ]
            if options["include_camera_trust"]:
                payload["camera_trust"]["entries"] = self._serialise_certs()

        if options["settings"]:
            payload["settings"] = {
                "config": self._serialise_settings(
                    settings,
                    include_webhook_secrets=options["include_webhook_secrets"],
                    include_tailscale_auth_key=options["include_tailscale_auth_key"],
                ),
                "hostname": self._read_hostname(settings.hostname),
            }

        return payload

    def _build_manifest(self, payload: dict, options: dict) -> dict:
        created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        salt = os.urandom(16)
        counts = {
            "users": len(payload["users"] or []),
            "cameras": len(payload["cameras"] or []),
            "webhook_destinations": len(
                (payload.get("settings") or {})
                .get("config", {})
                .get(
                    "webhook_destinations",
                    [],
                )
            ),
            "camera_trust_files": len(
                (payload.get("camera_trust") or {}).get("entries", [])
            ),
        }
        return {
            "format": BUNDLE_FORMAT,
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "created_at": created_at,
            "salt_b64": base64.b64encode(salt).decode("ascii"),
            "scope": {
                "users": options["users"],
                "cameras": options["cameras"],
                "settings": options["settings"],
                "camera_trust": options["cameras"] and options["include_camera_trust"],
            },
            "secret_policy": {
                "include_user_credentials": options["include_user_credentials"],
                "include_camera_trust": options["include_camera_trust"],
                "include_webhook_secrets": options["include_webhook_secrets"],
                "include_tailscale_auth_key": options["include_tailscale_auth_key"],
            },
            "counts": counts,
            "payload_sha256": hashlib.sha256(self._canonical_json(payload)).hexdigest(),
        }

    def _build_preview(self, manifest: dict, payload: dict) -> dict:
        users = payload.get("users") or []
        cameras = payload.get("cameras") or []
        settings_payload = payload.get("settings") or {}
        settings_config = settings_payload.get("config") or {}

        user_ids = {item.get("id", "") for item in users if isinstance(item, dict)}
        camera_ids = {item.get("id", "") for item in cameras if isinstance(item, dict)}

        current_users = self._store.get_users()
        current_cameras = self._store.get_cameras()
        current_settings = asdict(self._store.get_settings())

        current_user_ids = {user.id for user in current_users}
        current_camera_ids = {camera.id for camera in current_cameras}

        warnings: list[str] = []
        secret_policy = manifest.get("secret_policy", {})
        if not secret_policy.get("include_user_credentials", False):
            warnings.append(
                "User credentials are excluded; restored accounts will need new passwords."
            )
        if not secret_policy.get("include_webhook_secrets", False):
            warnings.append(
                "Webhook secrets are excluded; authenticated webhook destinations restore disabled."
            )
        if not secret_policy.get("include_tailscale_auth_key", False):
            warnings.append(
                "The Tailscale auth key is excluded; remote re-authentication may be required."
            )
        if not secret_policy.get("include_camera_trust", False) and manifest.get(
            "scope",
            {},
        ).get("cameras"):
            warnings.append(
                "Camera trust material is excluded; restored cameras will need re-pairing."
            )

        changed_setting_keys = sorted(
            key
            for key, value in settings_config.items()
            if current_settings.get(key) != value
        )

        return {
            "created_at": manifest.get("created_at", ""),
            "schema_version": manifest.get("schema_version"),
            "scope": manifest.get("scope", {}),
            "secret_policy": secret_policy,
            "warnings": warnings,
            "counts": manifest.get("counts", {}),
            "users": {
                "current": len(current_users),
                "incoming": len(users),
                "create": len(user_ids - current_user_ids),
                "update": len(user_ids & current_user_ids),
                "remove": len(current_user_ids - user_ids)
                if manifest.get("scope", {}).get("users")
                else 0,
                "accounts": [
                    {
                        "id": item.get("id", ""),
                        "username": item.get("username", ""),
                        "role": item.get("role", "viewer"),
                    }
                    for item in users
                ],
            },
            "cameras": {
                "current": len(current_cameras),
                "incoming": len(cameras),
                "create": len(camera_ids - current_camera_ids),
                "update": len(camera_ids & current_camera_ids),
                "remove": len(current_camera_ids - camera_ids)
                if manifest.get("scope", {}).get("cameras")
                else 0,
                "items": [
                    {
                        "id": item.get("id", ""),
                        "name": item.get("name", ""),
                        "location": item.get("location", ""),
                    }
                    for item in cameras
                ],
            },
            "settings": {
                "hostname": settings_payload.get("hostname", ""),
                "changed_keys": changed_setting_keys,
                "webhook_destination_count": len(
                    settings_config.get("webhook_destinations", [])
                ),
            },
            "restore_defaults": {
                "users": bool(manifest.get("scope", {}).get("users")),
                "cameras": bool(manifest.get("scope", {}).get("cameras")),
                "settings": bool(manifest.get("scope", {}).get("settings")),
                "camera_trust": bool(manifest.get("scope", {}).get("camera_trust")),
            },
        }

    def _sign_manifest_and_payload(
        self,
        *,
        manifest: dict,
        payload: dict,
        passphrase: str,
    ) -> str:
        key = self._derive_key(passphrase=passphrase, salt_b64=manifest["salt_b64"])
        body = self._canonical_json({"manifest": manifest, "payload": payload})
        return hmac.new(key, body, hashlib.sha256).hexdigest()

    def _load_bundle(self, bundle_bytes: bytes, *, passphrase: str) -> dict:
        self._validate_passphrase(passphrase)
        try:
            bundle = json.loads(bundle_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigBackupError(
                "Bundle is not valid JSON",
                reason="corrupt_bundle",
            ) from exc

        if not isinstance(bundle, dict):
            raise ConfigBackupError(
                "Bundle payload is malformed", reason="corrupt_bundle"
            )

        manifest = bundle.get("manifest")
        payload = bundle.get("payload")
        signature = bundle.get("signature", "")
        if not isinstance(manifest, dict) or not isinstance(payload, dict):
            raise ConfigBackupError(
                "Bundle payload is malformed", reason="corrupt_bundle"
            )
        if not isinstance(signature, str) or not signature:
            raise ConfigBackupError(
                "Bundle signature is missing", reason="corrupt_bundle"
            )

        if manifest.get("format") != BUNDLE_FORMAT:
            raise ConfigBackupError(
                "Bundle format is not supported by this server",
                reason="format_mismatch",
            )
        if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
            raise ConfigBackupError(
                "Bundle schema version does not match this server",
                reason="schema_mismatch",
            )

        expected_checksum = hashlib.sha256(self._canonical_json(payload)).hexdigest()
        if manifest.get("payload_sha256") != expected_checksum:
            raise ConfigBackupError(
                "Bundle contents do not match the signed manifest",
                reason="signature_mismatch",
            )

        expected_signature = self._sign_manifest_and_payload(
            manifest=manifest,
            payload=payload,
            passphrase=passphrase,
        )
        if not hmac.compare_digest(expected_signature, signature):
            raise ConfigBackupError(
                "Bundle signature verification failed",
                reason="signature_mismatch",
            )

        return {
            "manifest": manifest,
            "payload": payload,
            "signature": signature,
        }

    # ------------------------------------------------------------------
    # Restore paths and snapshots
    # ------------------------------------------------------------------
    def _normalise_payload(self, payload: dict, *, restore: dict) -> dict:
        users: list[User] = []
        cameras: list[Camera] = []
        settings: Settings | None = None
        hostname = ""
        cert_entries: list[dict] = []

        if restore["users"]:
            raw_users = payload.get("users")
            if not isinstance(raw_users, list):
                raise ConfigBackupError("Bundle users payload is malformed")
            users = [self._build_dataclass(User, item) for item in raw_users]
            if not any(user.role == "admin" for user in users):
                raise ConfigBackupError(
                    "Restoring users requires at least one admin account",
                    reason="invalid_users",
                )

        if restore["cameras"]:
            raw_cameras = payload.get("cameras")
            if not isinstance(raw_cameras, list):
                raise ConfigBackupError("Bundle cameras payload is malformed")
            cameras = [self._build_dataclass(Camera, item) for item in raw_cameras]

        if restore["settings"]:
            raw_settings = (payload.get("settings") or {}).get("config")
            if not isinstance(raw_settings, dict):
                raise ConfigBackupError("Bundle settings payload is malformed")
            settings = self._build_dataclass(Settings, raw_settings)
            hostname = str(
                (payload.get("settings") or {}).get("hostname") or ""
            ).strip()
            if not hostname:
                hostname = settings.hostname

        if restore["camera_trust"]:
            raw_entries = (payload.get("camera_trust") or {}).get("entries")
            if not isinstance(raw_entries, list):
                raise ConfigBackupError("Bundle camera trust payload is malformed")
            cert_entries = self._normalise_cert_entries(raw_entries)

        return {
            "users": users,
            "cameras": cameras,
            "settings": settings,
            "hostname": hostname,
            "camera_trust": cert_entries,
        }

    def _apply_import(self, *, payload: dict, restore: dict) -> None:
        targets = self._render_targets(payload=payload, restore=restore)
        for target in targets:
            if target["kind"] == "file":
                self._replace_file(target["path"], target["content"])
            else:
                self._replace_dir(target["path"], target["entries"])

        if restore["settings"] and payload["settings"] is not None:
            self._reapply_runtime_settings(payload["settings"])

    def _render_targets(self, *, payload: dict, restore: dict) -> list[dict]:
        targets: list[dict] = []
        if restore["users"]:
            targets.append(
                {
                    "name": "users",
                    "kind": "file",
                    "path": self._paths.users_file,
                    "content": self._render_json(
                        [asdict(user) for user in payload["users"]]
                    ),
                }
            )
        if restore["cameras"]:
            targets.append(
                {
                    "name": "cameras",
                    "kind": "file",
                    "path": self._paths.cameras_file,
                    "content": self._render_json(
                        [asdict(camera) for camera in payload["cameras"]]
                    ),
                }
            )
        if restore["settings"] and payload["settings"] is not None:
            targets.append(
                {
                    "name": "settings",
                    "kind": "file",
                    "path": self._paths.settings_file,
                    "content": self._render_json(asdict(payload["settings"])),
                }
            )
            targets.append(
                {
                    "name": "hostname",
                    "kind": "file",
                    "path": self._paths.hostname_file,
                    "content": f"{payload['hostname'].strip()}\n".encode(),
                }
            )
        if restore["camera_trust"]:
            targets.append(
                {
                    "name": "certs",
                    "kind": "dir",
                    "path": self._paths.certs_dir,
                    "entries": payload["camera_trust"],
                }
            )
        return targets

    def _create_snapshot(self, *, restore: dict, manifest: dict, payload: dict) -> dict:
        root = self._paths.backup_snapshot_root
        root.mkdir(parents=True, exist_ok=True)
        snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        snapshot_dir = root / f"{snapshot_id}-{os.urandom(3).hex()}"
        state_dir = snapshot_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        snapshot_created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        metadata = {
            "id": snapshot_dir.name,
            "created_at": snapshot_created_at,
            "bundle_created_at": manifest.get("created_at", ""),
            "restore_components": {
                key: bool(value) for key, value in restore.items() if value
            },
            "counts": {
                "users": len(payload["users"]) if restore["users"] else 0,
                "cameras": len(payload["cameras"]) if restore["cameras"] else 0,
                "camera_trust_files": len(payload["camera_trust"])
                if restore["camera_trust"]
                else 0,
            },
            "targets": [],
        }

        for target in self._render_targets(payload=payload, restore=restore):
            alias = self._snapshot_alias(target["name"])
            snapshot_target = state_dir / alias
            existed = target["path"].exists()
            metadata["targets"].append(
                {
                    "name": target["name"],
                    "kind": target["kind"],
                    "alias": alias,
                    "path": str(target["path"]),
                    "existed": existed,
                }
            )
            if not existed:
                continue
            if target["kind"] == "file":
                snapshot_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target["path"], snapshot_target)
            else:
                shutil.copytree(target["path"], snapshot_target)

        (snapshot_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2),
            encoding="utf-8",
        )
        return {
            "dir": snapshot_dir,
            "state_dir": state_dir,
            "metadata": metadata,
        }

    def _restore_snapshot(self, snapshot: dict) -> None:
        state_dir = snapshot["state_dir"]
        for target in snapshot["metadata"].get("targets", []):
            path = Path(target["path"])
            if path.exists():
                if target["kind"] == "file":
                    try:
                        path.unlink()
                    except OSError:
                        pass
                else:
                    shutil.rmtree(path, ignore_errors=True)

            if not target.get("existed"):
                continue

            snapshot_target = state_dir / target["alias"]
            if target["kind"] == "file":
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot_target, path)
            else:
                shutil.copytree(snapshot_target, path)

    def _prune_snapshots(self) -> None:
        keep = DEFAULT_SNAPSHOT_HISTORY
        try:
            keep = int(getattr(self._store.get_settings(), "backup_max_history", keep))
        except Exception:
            keep = DEFAULT_SNAPSHOT_HISTORY

        root = self._paths.backup_snapshot_root
        if not root.is_dir():
            return
        entries = sorted(
            [entry for entry in root.iterdir() if entry.is_dir()],
            reverse=True,
        )
        for stale in entries[keep:]:
            shutil.rmtree(stale, ignore_errors=True)

    # ------------------------------------------------------------------
    # Runtime helpers
    # ------------------------------------------------------------------
    def _reapply_runtime_settings(self, settings: Settings) -> None:
        if not self._settings_service:
            return
        updated_fields = {
            "timezone",
            "ntp_mode",
            "storage_threshold_percent",
            "clip_duration_seconds",
            "session_timeout_minutes",
            "loop_low_watermark_percent",
            "loop_hysteresis_percent",
        }
        try:
            self._settings_service._apply_runtime_changes(settings, updated_fields)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("Failed to reapply restored runtime settings: %s", exc)

    def _replace_file(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _replace_dir(self, path: Path, entries: list[dict]) -> None:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=f"{path.name}.", dir=str(parent)))
        try:
            for entry in entries:
                relative = PurePosixPath(entry["path"])
                target = staging_dir / Path(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(entry["content_b64"]))
            if path.exists():
                shutil.rmtree(path)
            shutil.move(str(staging_dir), str(path))
        finally:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------
    def _serialise_user(self, user: User, *, include_credentials: bool) -> dict:
        payload = asdict(user)
        if not include_credentials:
            payload["password_hash"] = ""
            payload["totp_secret"] = ""
            payload["must_change_password"] = True
            payload["failed_logins"] = 0
            payload["locked_until"] = ""
        return payload

    def _serialise_camera(self, camera: Camera, *, include_camera_trust: bool) -> dict:
        payload = asdict(camera)
        if not include_camera_trust:
            payload["pairing_secret"] = ""
            payload["cert_serial"] = ""
        return payload

    def _serialise_settings(
        self,
        settings: Settings,
        *,
        include_webhook_secrets: bool,
        include_tailscale_auth_key: bool,
    ) -> dict:
        payload = asdict(settings)
        if not include_tailscale_auth_key:
            payload["tailscale_auth_key"] = ""
        if not include_webhook_secrets:
            sanitised = []
            for destination in payload.get("webhook_destinations", []):
                if not isinstance(destination, dict):
                    continue
                cleaned = dict(destination)
                auth_type = cleaned.get("auth_type", "none")
                if auth_type != "none":
                    cleaned["secret"] = ""
                    cleaned["enabled"] = False
                sanitised.append(cleaned)
            payload["webhook_destinations"] = sanitised
        return payload

    def _serialise_certs(self) -> list[dict]:
        entries: list[dict] = []
        if not self._paths.certs_dir.is_dir():
            return entries
        for file_path in sorted(self._paths.certs_dir.rglob("*")):
            if not file_path.is_file():
                continue
            entries.append(
                {
                    "path": file_path.relative_to(self._paths.certs_dir).as_posix(),
                    "content_b64": base64.b64encode(file_path.read_bytes()).decode(
                        "ascii"
                    ),
                }
            )
        return entries

    def _normalise_cert_entries(self, entries: list[dict]) -> list[dict]:
        normalised: list[dict] = []
        for item in entries:
            if not isinstance(item, dict):
                raise ConfigBackupError("Bundle camera trust payload is malformed")
            rel_path = str(item.get("path", ""))
            content_b64 = str(item.get("content_b64", ""))
            if not rel_path or not content_b64:
                raise ConfigBackupError("Bundle camera trust payload is malformed")
            rel = PurePosixPath(rel_path)
            if rel.is_absolute() or ".." in rel.parts:
                raise ConfigBackupError(
                    "Bundle camera trust path is invalid",
                    reason="corrupt_bundle",
                )
            try:
                base64.b64decode(content_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ConfigBackupError(
                    "Bundle camera trust payload is malformed",
                    reason="corrupt_bundle",
                ) from exc
            normalised.append({"path": rel.as_posix(), "content_b64": content_b64})
        return normalised

    def _build_dataclass(self, cls, raw: dict):
        if not isinstance(raw, dict):
            raise ConfigBackupError(
                "Bundle payload is malformed", reason="corrupt_bundle"
            )
        allowed = {field.name for field in fields(cls)}
        filtered = {key: value for key, value in raw.items() if key in allowed}
        try:
            return cls(**filtered)
        except TypeError as exc:
            raise ConfigBackupError(
                "Bundle payload is malformed", reason="corrupt_bundle"
            ) from exc

    # ------------------------------------------------------------------
    # Request parsing helpers
    # ------------------------------------------------------------------
    def _normalise_export_options(self, options: dict | None) -> dict:
        options = options or {}
        scope = options.get("scope") if isinstance(options.get("scope"), dict) else {}
        result = {
            "users": self._as_bool(scope.get("users", options.get("users", True))),
            "cameras": self._as_bool(
                scope.get("cameras", options.get("cameras", True))
            ),
            "settings": self._as_bool(
                scope.get("settings", options.get("settings", True))
            ),
            "include_user_credentials": self._as_bool(
                options.get("include_user_credentials", False)
            ),
            "include_camera_trust": self._as_bool(
                options.get("include_camera_trust", True)
            ),
            "include_webhook_secrets": self._as_bool(
                options.get("include_webhook_secrets", False)
            ),
            "include_tailscale_auth_key": self._as_bool(
                options.get("include_tailscale_auth_key", False)
            ),
        }
        if not result["users"]:
            result["include_user_credentials"] = False
        if not result["cameras"]:
            result["include_camera_trust"] = False
        if not result["settings"]:
            result["include_webhook_secrets"] = False
            result["include_tailscale_auth_key"] = False
        if not any(result[key] for key in ("users", "cameras", "settings")):
            raise ConfigBackupError("Select at least one component to export")
        return result

    def _normalise_restore_options(
        self, options: dict | None, *, manifest: dict
    ) -> dict:
        options = options or {}
        scope = manifest.get("scope", {})
        result = {
            "users": self._as_bool(options["users"])
            if "users" in options
            else self._as_bool(scope.get("users", False)),
            "cameras": self._as_bool(options["cameras"])
            if "cameras" in options
            else self._as_bool(scope.get("cameras", False)),
            "settings": self._as_bool(options["settings"])
            if "settings" in options
            else self._as_bool(scope.get("settings", False)),
            "camera_trust": self._as_bool(options["camera_trust"])
            if "camera_trust" in options
            else self._as_bool(scope.get("camera_trust", False)),
        }
        available = {
            "users": self._as_bool(scope.get("users", False)),
            "cameras": self._as_bool(scope.get("cameras", False)),
            "settings": self._as_bool(scope.get("settings", False)),
            "camera_trust": self._as_bool(scope.get("camera_trust", False)),
        }
        for key, label in (
            ("users", "user data"),
            ("cameras", "camera data"),
            ("settings", "settings data"),
            ("camera_trust", "camera trust material"),
        ):
            if result[key] and not available[key]:
                raise ConfigBackupError(
                    f"Bundle does not contain {label}",
                    reason="invalid_restore_scope",
                )
        if result["camera_trust"] and not result["cameras"]:
            raise ConfigBackupError(
                "Camera trust restore requires camera restore",
                reason="invalid_restore_scope",
            )
        if not any(result.values()):
            raise ConfigBackupError("Select at least one component to restore")
        return result

    def _validate_passphrase(self, passphrase: str) -> None:
        if (
            not isinstance(passphrase, str)
            or len(passphrase.strip()) < MIN_PASSPHRASE_LENGTH
        ):
            raise ConfigBackupError(
                f"Passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters",
                reason="weak_passphrase",
            )

    def _derive_key(self, *, passphrase: str, salt_b64: str) -> bytes:
        self._validate_passphrase(passphrase)
        try:
            salt = base64.b64decode(salt_b64)
        except (binascii.Error, ValueError) as exc:
            raise ConfigBackupError(
                "Bundle salt is invalid", reason="corrupt_bundle"
            ) from exc
        return hashlib.pbkdf2_hmac(
            "sha256",
            passphrase.encode("utf-8"),
            salt,
            PBKDF2_ITERATIONS,
            dklen=32,
        )

    # ------------------------------------------------------------------
    # Small utilities
    # ------------------------------------------------------------------
    @staticmethod
    def _canonical_json(payload: dict) -> bytes:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

    @staticmethod
    def _render_json(payload: dict | list) -> bytes:
        return (json.dumps(payload, indent=2, default=str) + "\n").encode("utf-8")

    @staticmethod
    def _as_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _read_hostname(self, default: str) -> str:
        try:
            value = self._paths.hostname_file.read_text(encoding="utf-8").strip()
            return value or default
        except OSError:
            return default

    @staticmethod
    def _snapshot_alias(name: str) -> str:
        mapping = {
            "users": "config/users.json",
            "cameras": "config/cameras.json",
            "settings": "config/settings.json",
            "hostname": "config/hostname",
            "certs": "certs",
        }
        return mapping[name]
