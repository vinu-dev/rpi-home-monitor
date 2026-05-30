# REQ: SWR-018, SWR-050; RISK: RISK-006, RISK-018; SEC: SC-006, SC-019; TEST: TC-015, TC-044
"""Unit tests for the camera privileged helper allowlist."""

import os
from unittest.mock import MagicMock, patch

import pytest

from camera_streamer import privileged_helper


def test_hotspot_wipe_uses_camera_hotspot_script():
    with patch("camera_streamer.privileged_helper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        data = privileged_helper._op_hotspot_wipe({})

    assert data["returncode"] == 0
    mock_run.assert_called_once_with(
        ["/opt/camera/scripts/camera-hotspot.sh", "wipe"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_hotspot_start_restarts_camera_hotspot_service():
    with patch("camera_streamer.privileged_helper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        data = privileged_helper._op_hotspot_start({})

    assert data["returncode"] == 0
    mock_run.assert_called_once_with(
        ["systemctl", "restart", "camera-hotspot.service"],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def test_hotspot_stop_uses_camera_hotspot_script():
    with patch("camera_streamer.privileged_helper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        data = privileged_helper._op_hotspot_stop({})

    assert data["returncode"] == 0
    mock_run.assert_called_once_with(
        ["/opt/camera/scripts/camera-hotspot.sh", "stop"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_hotspot_connect_uses_camera_hotspot_script():
    with patch("camera_streamer.privileged_helper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        data = privileged_helper._op_hotspot_connect(
            {"ssid": "MysticNet2.4", "password": "secret-pass"}
        )

    assert data["returncode"] == 0
    mock_run.assert_called_once_with(
        [
            "/opt/camera/scripts/camera-hotspot.sh",
            "connect",
            "MysticNet2.4",
            "secret-pass",
        ],
        capture_output=True,
        text=True,
        timeout=75,
        check=False,
    )


def test_hotspot_connect_rejects_empty_payload():
    with pytest.raises(privileged_helper.HelperRequestError, match="ssid required"):
        privileged_helper._op_hotspot_connect({"password": "secret-pass"})


def test_hotspot_set_password_writes_protected_file(tmp_path):
    target = tmp_path / "config" / "camera-hotspot.psk"
    mock_pwd = MagicMock()
    mock_grp = MagicMock()
    mock_pwd.getpwnam.return_value = MagicMock(pw_uid=321)
    mock_grp.getgrnam.return_value = MagicMock(gr_gid=654)

    with (
        patch.object(privileged_helper, "SETUP_HOTSPOT_PASSWORD_FILE", str(target)),
        patch.object(privileged_helper, "pwd", mock_pwd),
        patch.object(privileged_helper, "grp", mock_grp),
        patch.object(privileged_helper.os, "chown", create=True) as mock_chown,
    ):
        data = privileged_helper._op_hotspot_set_password(
            {"password": "CameraSetupPass123"}
        )

    assert data == {"returncode": 0, "path": str(target)}
    assert target.read_text(encoding="utf-8") == "CameraSetupPass123\n"
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    mock_chown.assert_any_call(str(target), 321, 654)


def test_hotspot_set_password_rejects_factory_default():
    with pytest.raises(privileged_helper.HelperRequestError, match="new setup"):
        privileged_helper._op_hotspot_set_password({"password": "homecamera"})


def test_hotspot_set_password_rejects_short_password():
    with pytest.raises(privileged_helper.HelperRequestError, match="at least 12"):
        privileged_helper._op_hotspot_set_password({"password": "short"})


def test_system_reboot_uses_systemctl_reboot():
    with patch("camera_streamer.privileged_helper.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        data = privileged_helper._op_system_reboot({})

    assert data["returncode"] == 0
    mock_run.assert_called_once_with(
        ["systemctl", "reboot"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_rejects_unknown_operation():
    with pytest.raises(privileged_helper.HelperRequestError, match="not allowed"):
        privileged_helper._handle_request(b'{"operation":"not.allowed","payload":{}}')
