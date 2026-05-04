# REQ: SWR-024, SWR-057; RISK: RISK-012, RISK-017, RISK-020; SEC: SC-012, SC-020; TEST: TC-023, TC-041, TC-049
"""Unit tests for the offsite-backup service."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from monitor.models import Settings
from monitor.services.offsite_backup import OffsiteBackupService, RemoteObject
from monitor.store import Store


def _make_service(tmp_path, settings=None, client_factory=None, audit=None):
    config_dir = tmp_path / "config"
    recordings_dir = tmp_path / "recordings"
    config_dir.mkdir(parents=True, exist_ok=True)
    recordings_dir.mkdir(parents=True, exist_ok=True)

    store = Store(str(config_dir))
    store.save_settings(settings or Settings())
    service = OffsiteBackupService(
        store=store,
        audit=audit or MagicMock(),
        config_dir=str(config_dir),
        recordings_dir=str(recordings_dir),
        client_factory=client_factory,
    )
    return service, store, recordings_dir


def _write_clip(recordings_dir, camera_id="cam-001", stem="20260504_101500"):
    cam_dir = recordings_dir / camera_id
    cam_dir.mkdir(parents=True, exist_ok=True)
    path = cam_dir / f"{stem}.mp4"
    path.write_bytes(b"clip-bytes")
    old_enough = datetime.now().timestamp() - 30
    os.utime(path, (old_enough, old_enough))
    return path


def _configured_settings(**overrides):
    settings = Settings(
        offsite_backup_enabled=True,
        offsite_backup_endpoint="s3.amazonaws.com",
        offsite_backup_bucket="hm-backups",
        offsite_backup_access_key_id="AKIATEST",
        offsite_backup_secret_access_key="secret-value",
        offsite_backup_prefix="backups/home-monitor",
        offsite_backup_retention_days=30,
        offsite_backup_bandwidth_cap_mbps=5.0,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class TestStatus:
    def test_status_redacts_secret_but_reports_queue_state(self, tmp_path):
        service, _store, _recordings_dir = _make_service(
            tmp_path,
            settings=_configured_settings(),
        )
        service._save_state(
            {
                "pending": [{"clip_id": "cam-001/2026-05-04/a.mp4"}],
                "uploaded": {},
                "failed": [{"clip_id": "cam-001/2026-05-04/b.mp4"}],
                "last_success_at": "2026-05-04T10:15:00Z",
                "last_error": "Could not reach the remote storage endpoint",
                "last_error_at": "2026-05-04T10:20:00Z",
                "last_retention_cleanup_at": "",
            }
        )

        result = service.get_settings_status()

        assert result["enabled"] is True
        assert result["secret_configured"] is True
        assert result["queue_size"] == 1
        assert result["failed_count"] == 1
        assert "secret_access_key" not in result


class TestConfigUpdate:
    def test_update_rejects_http_endpoint(self, tmp_path):
        service, _store, _recordings_dir = _make_service(tmp_path)
        msg, code = service.update_config(
            {
                "enabled": True,
                "endpoint": "http://minio.example.com:9000",
                "bucket": "hm-backups",
                "access_key_id": "AKIA123",
                "secret_access_key": "secret-value",
            }
        )

        assert code == 400
        assert "HTTPS" in msg

    def test_update_preserves_existing_secret_when_blank(self, tmp_path):
        service, store, _recordings_dir = _make_service(
            tmp_path,
            settings=_configured_settings(),
        )

        msg, code = service.update_config(
            {
                "enabled": True,
                "endpoint": "minio.example.com:9000",
                "bucket": "hm-backups",
                "access_key_id": "AKIAUPDATED",
                "secret_access_key": "",
                "prefix": "new-prefix",
            }
        )

        saved = store.get_settings()
        assert code == 200
        assert "updated" in msg.lower()
        assert saved.offsite_backup_secret_access_key == "secret-value"
        assert saved.offsite_backup_access_key_id == "AKIAUPDATED"
        assert saved.offsite_backup_prefix == "new-prefix"


class TestProbe:
    def test_test_connection_creates_and_deletes_probe(self, tmp_path):
        fake_client = MagicMock()
        service, _store, _recordings_dir = _make_service(
            tmp_path,
            settings=_configured_settings(),
            client_factory=lambda _config: fake_client,
        )

        msg, code = service.test_connection({})

        assert code == 200
        assert msg == "Connection OK"
        fake_client.write_probe.assert_called_once()
        fake_client.delete_object.assert_called_once()


class TestSync:
    def test_run_once_uploads_finalized_clips(self, tmp_path):
        fake_client = MagicMock()
        audit = MagicMock()
        service, _store, recordings_dir = _make_service(
            tmp_path,
            settings=_configured_settings(),
            client_factory=lambda _config: fake_client,
            audit=audit,
        )
        path = _write_clip(recordings_dir)

        service.run_once()

        state = service._load_state()
        assert state["pending"] == []
        assert list(state["uploaded"]) == ["cam-001/2026-05-04/20260504_101500.mp4"]
        fake_client.upload_file.assert_called_once()
        upload_args = fake_client.upload_file.call_args.args
        assert upload_args[0] == "hm-backups"
        assert upload_args[2] == str(path)
        audit.log_event.assert_any_call(
            "BACKUP_SUCCESS",
            user="",
            ip="",
            detail="uploaded cam-001/2026-05-04/20260504_101500.mp4",
        )

    def test_run_once_records_retry_after_failed_upload(self, tmp_path):
        class _Boom:
            def upload_file(self, *_args, **_kwargs):
                raise RuntimeError("AccessDenied")

            def iter_objects(self, *_args, **_kwargs):
                return []

        service, _store, recordings_dir = _make_service(
            tmp_path,
            settings=_configured_settings(),
            client_factory=lambda _config: _Boom(),
        )
        _write_clip(recordings_dir)

        service.run_once()

        state = service._load_state()
        assert len(state["pending"]) == 1
        assert state["pending"][0]["attempts"] == 1
        assert state["pending"][0]["next_attempt_at"] != ""
        assert "credentials" in state["last_error"].lower()

    def test_run_once_prunes_old_remote_objects_on_retention_sweep(self, tmp_path):
        deleted = []

        class _Client:
            def upload_file(self, *_args, **_kwargs):
                return None

            def iter_objects(self, *_args, **_kwargs):
                return [
                    RemoteObject(
                        key="backups/home-monitor/cam-001/old.mp4",
                        last_modified=datetime.now(UTC) - timedelta(days=10),
                    ),
                    RemoteObject(
                        key="backups/home-monitor/cam-001/new.mp4",
                        last_modified=datetime.now(UTC) - timedelta(days=1),
                    ),
                ]

            def delete_object(self, _bucket, key):
                deleted.append(key)

        service, _store, _recordings_dir = _make_service(
            tmp_path,
            settings=_configured_settings(
                offsite_backup_retention_days=7,
            ),
            client_factory=lambda _config: _Client(),
        )

        service.run_once()

        assert deleted == ["backups/home-monitor/cam-001/old.mp4"]
