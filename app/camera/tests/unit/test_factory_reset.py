# REQ: SWR-018; RISK: RISK-006; SEC: SC-006; TEST: TC-015
"""Unit tests for camera FactoryResetService — wipe data and return to first-boot."""

import os
from unittest.mock import MagicMock, patch

import pytest

from camera_streamer.factory_reset import FactoryResetService


@pytest.fixture
def data_dir(tmp_path):
    """Create a realistic /data directory structure for camera."""
    dirs = ["config", "certs", "logs", "ota"]
    for d in dirs:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)

    # Config file
    (tmp_path / "config" / "camera.conf").write_text("[camera]\nid=cam-001\n")
    (tmp_path / "config" / "camera-hotspot.psk").write_text("CameraSetupPass123\n")

    # Stamp file
    (tmp_path / ".setup-done").write_text("1\n")

    # Certs (pairing data)
    (tmp_path / "certs" / "client.crt").write_text("cert")
    (tmp_path / "certs" / "client.key").write_text("key")

    # Logs
    (tmp_path / "logs" / "camera.log").write_text("log entry\n")

    return tmp_path


@pytest.fixture
def config():
    mock = MagicMock()
    mock.data_dir = "/data"
    return mock


@pytest.fixture
def svc(config, data_dir):
    return FactoryResetService(config, str(data_dir), hotspot_script="/nonexistent")


class TestCameraFactoryReset:
    """Test the full factory reset flow."""

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_removes_stamp_file(self, mock_reboot, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / ".setup-done").exists()

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_removes_config(self, mock_reboot, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "config" / "camera.conf").exists()

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_preserves_setup_hotspot_password(self, mock_reboot, svc, data_dir):
        svc.execute_reset()
        assert (data_dir / "config" / "camera-hotspot.psk").read_text() == (
            "CameraSetupPass123\n"
        )

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_removes_certs(self, mock_reboot, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "certs").exists()

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_removes_logs(self, mock_reboot, svc, data_dir):
        svc.execute_reset()
        assert not (data_dir / "logs").exists()

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_removes_ota(self, mock_reboot, svc, data_dir):
        (data_dir / "ota" / "update.swu").write_bytes(b"\x00" * 50)
        svc.execute_reset()
        assert not (data_dir / "ota").exists()

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_returns_200(self, mock_reboot, svc):
        msg, status = svc.execute_reset()
        assert status == 200
        assert "reset" in msg.lower()

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_schedules_reboot(self, mock_reboot, svc):
        svc.execute_reset()
        mock_reboot.assert_called_once()

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_idempotent(self, mock_reboot, svc, data_dir):
        """Running reset twice doesn't fail."""
        svc.execute_reset()
        msg, status = svc.execute_reset()
        assert status == 200

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_on_empty_data_dir(self, mock_reboot, config, tmp_path):
        """Reset works even if /data is empty."""
        svc = FactoryResetService(config, str(tmp_path), hotspot_script="/nonexistent")
        msg, status = svc.execute_reset()
        assert status == 200


class TestWifiWipeDelegation:
    """Test that factory reset delegates WiFi wipe to hotspot script."""

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    @patch("camera_streamer.factory_reset.subprocess.run")
    @patch("camera_streamer.factory_reset.os.path.isfile")
    def test_calls_hotspot_wipe(
        self, mock_isfile, mock_run, mock_reboot, config, tmp_path
    ):
        """WiFi wipe delegates to hotspot script's 'wipe' command."""
        original_isfile = os.path.isfile

        def isfile_side_effect(path):
            if path == "/opt/camera/scripts/camera-hotspot.sh":
                return True
            return original_isfile(path)

        mock_isfile.side_effect = isfile_side_effect
        mock_run.return_value = MagicMock(returncode=0)

        svc = FactoryResetService(
            config,
            str(tmp_path),
            hotspot_script="/opt/camera/scripts/camera-hotspot.sh",
        )
        svc.execute_reset()

        # Find the wipe call among all subprocess.run calls
        wipe_calls = [
            c
            for c in mock_run.call_args_list
            if c[0][0] == ["/opt/camera/scripts/camera-hotspot.sh", "wipe"]
        ]
        assert len(wipe_calls) == 1

    @patch("camera_streamer.factory_reset.FactoryResetService._schedule_reboot")
    def test_reset_succeeds_without_hotspot_script(self, mock_reboot, config, tmp_path):
        """Reset doesn't fail if hotspot script doesn't exist."""
        svc = FactoryResetService(config, str(tmp_path), hotspot_script="/nonexistent")
        msg, status = svc.execute_reset()
        assert status == 200


class TestPostResetRecovery:
    """Camera reset must not strand first-boot setup if reboot fails."""

    @patch("camera_streamer.factory_reset.subprocess.run")
    def test_attempt_reboot_checks_return_code(self, mock_run, config, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="denied")
        svc = FactoryResetService(config, str(tmp_path), hotspot_script="/nonexistent")

        assert svc._attempt_reboot() is False

        mock_run.assert_called_once_with(
            ["systemctl", "reboot"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    @patch("camera_streamer.factory_reset.privileged.request")
    @patch("camera_streamer.factory_reset.privileged.should_use_helper")
    def test_attempt_reboot_uses_privileged_helper(
        self, mock_should_use_helper, mock_request, config, tmp_path
    ):
        mock_should_use_helper.return_value = True
        mock_request.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        svc = FactoryResetService(config, str(tmp_path), hotspot_script="/nonexistent")

        assert svc._attempt_reboot() is True

        mock_request.assert_called_once_with("system.reboot", timeout=20)

    def test_post_reset_recovery_starts_hotspot_when_reboot_fails(
        self, config, tmp_path
    ):
        svc = FactoryResetService(config, str(tmp_path), hotspot_script="/nonexistent")

        with (
            patch.object(svc, "_attempt_reboot", return_value=False) as reboot,
            patch.object(svc, "_start_setup_hotspot", return_value=True) as hotspot,
        ):
            svc._run_post_reset_recovery()

        reboot.assert_called_once()
        hotspot.assert_called_once()

    @patch("camera_streamer.factory_reset.subprocess.run")
    def test_start_setup_hotspot_restarts_systemd(self, mock_run, config, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        svc = FactoryResetService(config, str(tmp_path), hotspot_script="/nonexistent")

        assert svc._start_setup_hotspot() is True

        mock_run.assert_called_once_with(
            ["systemctl", "restart", "camera-hotspot.service"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )

    @patch("camera_streamer.factory_reset.privileged.request")
    @patch("camera_streamer.factory_reset.privileged.should_use_helper")
    def test_start_setup_hotspot_uses_privileged_helper(
        self, mock_should_use_helper, mock_request, config, tmp_path
    ):
        mock_should_use_helper.return_value = True
        mock_request.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        svc = FactoryResetService(config, str(tmp_path), hotspot_script="/nonexistent")

        assert svc._start_setup_hotspot() is True

        mock_request.assert_called_once_with("hotspot.start", timeout=95)


class TestPrivilegedWifiWipe:
    @patch("camera_streamer.factory_reset.privileged.request")
    @patch("camera_streamer.factory_reset.privileged.should_use_helper")
    def test_clear_wifi_uses_privileged_helper(
        self, mock_should_use_helper, mock_request, config, tmp_path
    ):
        mock_should_use_helper.return_value = True
        mock_request.return_value = {"returncode": 0, "stdout": "", "stderr": ""}
        svc = FactoryResetService(config, str(tmp_path), hotspot_script="/nonexistent")
        errors = []

        svc._clear_wifi(errors)

        mock_request.assert_called_once_with("hotspot.wipe", timeout=35)
        assert errors == []
