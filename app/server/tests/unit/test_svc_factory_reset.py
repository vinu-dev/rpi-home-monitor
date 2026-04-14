"""Unit tests for FactoryResetService — wipe data and return to first-boot."""

from unittest.mock import MagicMock, patch

import pytest

from monitor.services.factory_reset_service import FactoryResetService


@pytest.fixture
def data_dir(tmp_path):
    """Create a realistic /data directory structure."""
    dirs = [
        "config",
        "certs",
        "certs/cameras",
        "live",
        "recordings",
        "logs",
        "tailscale",
        "ota",
    ]
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    # Config files
    (tmp_path / "config" / "cameras.json").write_text("[]")
    (tmp_path / "config" / "users.json").write_text("[]")
    (tmp_path / "config" / "settings.json").write_text("{}")
    (tmp_path / "config" / ".secret_key").write_text("abc123")

    # Stamp file
    (tmp_path / ".setup-done").write_text("setup completed\n")

    # Certs
    (tmp_path / "certs" / "ca.crt").write_text("cert")
    (tmp_path / "certs" / "server.crt").write_text("cert")
    (tmp_path / "certs" / "cameras" / "cam-001.crt").write_text("cert")

    # Recordings
    rec_dir = tmp_path / "recordings" / "cam-001" / "2026-04-12"
    rec_dir.mkdir(parents=True)
    (rec_dir / "14-00-00.mp4").write_bytes(b"\x00" * 100)

    # Live buffer
    (tmp_path / "live" / "stream.m3u8").write_text("playlist")

    # Logs
    (tmp_path / "logs" / "audit.log").write_text("event\n")

    return tmp_path


@pytest.fixture
def store():
    return MagicMock()


@pytest.fixture
def audit():
    return MagicMock()


@pytest.fixture
def svc(store, audit, data_dir):
    return FactoryResetService(store, audit, str(data_dir))


class TestFactoryReset:
    """Test the full factory reset flow."""

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_removes_stamp_file(self, mock_restart, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / ".setup-done").exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_removes_config_files(self, mock_restart, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "config" / "cameras.json").exists()
        assert not (data_dir / "config" / "users.json").exists()
        assert not (data_dir / "config" / "settings.json").exists()
        assert not (data_dir / "config" / ".secret_key").exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_removes_certs(self, mock_restart, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "certs").exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_removes_recordings_by_default(self, mock_restart, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "recordings").exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_keeps_recordings_when_requested(self, mock_restart, svc, data_dir):
        svc.execute_reset(keep_recordings=True)
        assert (
            data_dir / "recordings" / "cam-001" / "2026-04-12" / "14-00-00.mp4"
        ).exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_removes_live_buffer(self, mock_restart, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "live").exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_removes_logs(self, mock_restart, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "logs").exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_removes_tailscale_state(self, mock_restart, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "tailscale").exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_removes_ota_staging(self, mock_restart, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "ota").exists()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_returns_200(self, mock_restart, svc):
        msg, status = svc.execute_reset()
        assert status == 200
        assert "reset" in msg.lower()

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_schedules_restart(self, mock_restart, svc):
        svc.execute_reset()
        mock_restart.assert_called_once()


class TestAuditLogging:
    """Test that factory reset logs audit events."""

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_audit_logged_before_wipe(self, mock_restart, svc, audit):
        svc.execute_reset(requesting_user="admin", requesting_ip="10.0.0.1")
        audit.log_event.assert_called_once()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "FACTORY_RESET"
        assert call_args[1]["user"] == "admin"
        assert call_args[1]["ip"] == "10.0.0.1"

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_audit_includes_keep_recordings_flag(self, mock_restart, svc, audit):
        svc.execute_reset(keep_recordings=True)
        detail = audit.log_event.call_args[1]["detail"]
        assert "keep_recordings=True" in detail

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_no_audit_service_does_not_raise(self, mock_restart, store, data_dir):
        svc = FactoryResetService(store, None, str(data_dir))
        msg, status = svc.execute_reset()
        assert status == 200

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_audit_failure_does_not_break_reset(self, mock_restart, store, data_dir):
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("audit db down")
        svc = FactoryResetService(store, audit, str(data_dir))
        msg, status = svc.execute_reset()
        assert status == 200


class TestEdgeCases:
    """Test factory reset with missing or already-clean state."""

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_on_empty_data_dir(self, mock_restart, store, audit, tmp_path):
        """Reset works even if /data is empty (fresh state)."""
        svc = FactoryResetService(store, audit, str(tmp_path))
        msg, status = svc.execute_reset()
        assert status == 200

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_reset_idempotent(self, mock_restart, svc, data_dir):
        """Running reset twice doesn't fail."""
        svc.execute_reset()
        msg, status = svc.execute_reset()
        assert status == 200

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    def test_config_dir_preserved(self, mock_restart, svc, data_dir):
        """Config directory itself is not removed (only its contents)."""
        svc.execute_reset()
        assert (data_dir / "config").exists()


class TestWifiWipeDelegation:
    """Test that factory reset delegates WiFi wipe to hotspot script (ADR-0013)."""

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    @patch("monitor.services.factory_reset_service.subprocess.run")
    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._find_hotspot_script"
    )
    def test_reset_calls_hotspot_wipe(
        self, mock_find, mock_run, mock_restart, svc, data_dir
    ):
        """WiFi wipe delegates to hotspot script's 'wipe' command."""
        mock_find.return_value = "/opt/monitor/scripts/monitor-hotspot.sh"
        mock_run.return_value = MagicMock(returncode=0)
        svc.execute_reset()
        wipe_calls = [
            c
            for c in mock_run.call_args_list
            if len(c[0]) > 0
            and c[0][0] == ["/opt/monitor/scripts/monitor-hotspot.sh", "wipe"]
        ]
        assert len(wipe_calls) == 1

    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._schedule_restart"
    )
    @patch(
        "monitor.services.factory_reset_service.FactoryResetService._find_hotspot_script"
    )
    def test_reset_succeeds_without_hotspot_script(self, mock_find, mock_restart, svc):
        """Reset doesn't fail if hotspot script is not found."""
        mock_find.return_value = None
        msg, status = svc.execute_reset()
        assert status == 200
