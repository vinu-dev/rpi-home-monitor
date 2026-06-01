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


def test_hostname_set_validates_and_updates_kernel_and_avahi(tmp_path):
    target = tmp_path / "config" / "hostname"
    mock_pwd = MagicMock()
    mock_grp = MagicMock()
    mock_pwd.getpwnam.return_value = MagicMock(pw_uid=321)
    mock_grp.getgrnam.return_value = MagicMock(gr_gid=654)

    with (
        patch.object(privileged_helper, "CAMERA_HOSTNAME_FILE", str(target)),
        patch.object(privileged_helper, "pwd", mock_pwd),
        patch.object(privileged_helper, "grp", mock_grp),
        patch.object(privileged_helper.os, "chown", create=True),
        patch("camera_streamer.privileged_helper.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        data = privileged_helper._op_hostname_set({"hostname": "rpi-divinu-cam-a5cf"})

    assert data["hostname"] == "rpi-divinu-cam-a5cf"
    assert target.read_text(encoding="utf-8") == "rpi-divinu-cam-a5cf\n"
    assert [call.args[0] for call in mock_run.call_args_list] == [
        ["hostname", "rpi-divinu-cam-a5cf"],
        ["avahi-set-host-name", "rpi-divinu-cam-a5cf"],
    ]


@pytest.mark.parametrize(
    "bad_hostname",
    ["", "-bad", "bad-", "bad_name", "bad.name", "a" * 64],
)
def test_hostname_set_rejects_invalid_hostnames(bad_hostname):
    with pytest.raises(privileged_helper.HelperRequestError, match="hostname"):
        privileged_helper._op_hostname_set({"hostname": bad_hostname})


def test_hostname_set_restarts_avahi_when_dbus_update_fails(tmp_path):
    target = tmp_path / "config" / "hostname"
    results = [
        MagicMock(returncode=0, stdout="", stderr=""),
        MagicMock(returncode=1, stdout="", stderr="Access denied"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ]

    with (
        patch.object(privileged_helper, "CAMERA_HOSTNAME_FILE", str(target)),
        patch.object(privileged_helper, "_camera_identity", return_value=None),
        patch("camera_streamer.privileged_helper.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = results
        data = privileged_helper._op_hostname_set({"hostname": "rpi-divinu-cam-a5cf"})

    assert data["avahi_returncode"] == 1
    assert [call.args[0] for call in mock_run.call_args_list] == [
        ["hostname", "rpi-divinu-cam-a5cf"],
        ["avahi-set-host-name", "rpi-divinu-cam-a5cf"],
        ["systemctl", "restart", "avahi-daemon"],
    ]


def test_hostname_set_treats_avahi_redundant_as_success(tmp_path):
    target = tmp_path / "config" / "hostname"
    results = [
        MagicMock(returncode=0, stdout="", stderr=""),
        MagicMock(
            returncode=1,
            stdout="",
            stderr=(
                "Failed to create host name resolver: The requested operation "
                "is invalid because redundant"
            ),
        ),
    ]

    with (
        patch.object(privileged_helper, "CAMERA_HOSTNAME_FILE", str(target)),
        patch.object(privileged_helper, "_camera_identity", return_value=None),
        patch("camera_streamer.privileged_helper.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = results
        data = privileged_helper._op_hostname_set({"hostname": "rpi-divinu-cam-a5cf"})

    assert data["avahi_returncode"] == 1
    assert data["avahi_effective"] == "ok"
    assert [call.args[0] for call in mock_run.call_args_list] == [
        ["hostname", "rpi-divinu-cam-a5cf"],
        ["avahi-set-host-name", "rpi-divinu-cam-a5cf"],
    ]


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
