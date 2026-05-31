# REQ: SWR-027, SWR-028, SWR-063; RISK: RISK-013, RISK-018; SEC: SC-013, SC-019; TEST: TC-024, TC-025, TC-044
"""Tests for the monitor privileged helper allowlist."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from monitor.services import privileged_helper as helper


def test_unknown_operation_is_rejected():
    with pytest.raises(helper.HelperRequestError, match="allowlisted"):
        helper.handle_request(b'{"operation":"shell.exec","payload":{}}')


def test_rejects_malformed_requests():
    with pytest.raises(helper.HelperRequestError, match="too large"):
        helper.handle_request(b"x" * (helper.MAX_REQUEST_BYTES + 1))
    with pytest.raises(helper.HelperRequestError, match="invalid JSON"):
        helper.handle_request(b"{")
    with pytest.raises(helper.HelperRequestError, match="object"):
        helper.handle_request(b"[]")
    with pytest.raises(helper.HelperRequestError, match="payload"):
        helper.handle_request(b'{"operation":"system.reboot","payload":"bad"}')


def test_text_payload_validation_rejects_non_strings_and_empty_values():
    with pytest.raises(helper.HelperRequestError, match="string"):
        helper._as_text(123)
    with pytest.raises(helper.HelperRequestError, match="length"):
        helper._as_text("   ")


def test_usb_mount_rejects_non_usb_device():
    request = b'{"operation":"usb.mount","payload":{"device_path":"/dev/mmcblk0p1"}}'
    with pytest.raises(helper.HelperRequestError, match="allowed USB"):
        helper.handle_request(request)


def test_usb_mount_rejects_non_allowlisted_mount_point():
    request = (
        b'{"operation":"usb.mount","payload":'
        b'{"device_path":"/dev/sda1","mount_point":"/etc"}}'
    )
    with (
        patch.object(
            helper.usb,
            "detect_devices",
            return_value=[{"path": "/dev/sda1"}],
        ),
        pytest.raises(helper.HelperRequestError, match="mount point"),
    ):
        helper.handle_request(request)


def test_usb_mount_rejects_device_not_detected_as_usb():
    request = b'{"operation":"usb.mount","payload":{"device_path":"/dev/sda1"}}'
    with (
        patch.object(helper.usb, "detect_devices", return_value=[]),
        pytest.raises(helper.HelperRequestError, match="removable USB"),
    ):
        helper.handle_request(request)


def test_usb_mount_delegates_to_usb_module():
    with (
        patch.object(
            helper.usb,
            "detect_devices",
            return_value=[{"path": "/dev/sda1"}],
        ),
        patch.object(helper.usb, "mount_device", return_value=(True, "")) as mount,
        patch.object(helper, "_ensure_monitor_can_write_mount") as ensure_writable,
    ):
        data = helper.handle_request(
            b'{"operation":"usb.mount","payload":{"device_path":"/dev/sda1"}}'
        )

    assert data == {}
    mount.assert_called_once_with("/dev/sda1", helper.usb.DEFAULT_MOUNT_POINT)
    ensure_writable.assert_called_once_with(helper.usb.DEFAULT_MOUNT_POINT)


def test_usb_detect_delegates_to_usb_module():
    devices = [{"path": "/dev/sda1", "fstype": "ext4", "supported": True}]
    with patch.object(helper.usb, "detect_devices", return_value=devices) as detect:
        assert helper.handle_request(b'{"operation":"usb.detect","payload":{}}') == {
            "devices": devices
        }
    detect.assert_called_once_with()


def test_usb_mount_fails_when_helper_cannot_make_mount_writable():
    with (
        patch.object(
            helper.usb,
            "detect_devices",
            return_value=[{"path": "/dev/sda1"}],
        ),
        patch.object(helper.usb, "mount_device", return_value=(True, "")),
        patch.object(
            helper,
            "_ensure_monitor_can_write_mount",
            side_effect=helper.HelperRequestError("not writable"),
        ),
        pytest.raises(helper.HelperRequestError, match="not writable"),
    ):
        helper.handle_request(
            b'{"operation":"usb.mount","payload":{"device_path":"/dev/sda1"}}'
        )


def test_helper_mount_ownership_covers_recordings_folder():
    fake_pwd = MagicMock()
    fake_pwd.getpwnam.return_value.pw_uid = 996
    fake_grp = MagicMock()
    fake_grp.getgrnam.return_value.gr_gid = 994

    with (
        patch.object(helper, "pwd", fake_pwd),
        patch.object(helper, "grp", fake_grp),
        patch.object(helper.os, "chown", create=True) as chown,
        patch.object(helper.os, "chmod") as chmod,
        patch.object(helper.os, "makedirs") as makedirs,
    ):
        helper._ensure_monitor_can_write_mount("/mnt/recordings")

    makedirs.assert_called_once_with(
        f"/mnt/recordings/{helper.usb.RECORDINGS_FOLDER}", exist_ok=True
    )
    chown.assert_any_call("/mnt/recordings", 996, 994)
    chown.assert_any_call(f"/mnt/recordings/{helper.usb.RECORDINGS_FOLDER}", 996, 994)
    chmod.assert_any_call("/mnt/recordings", 0o775)
    chmod.assert_any_call(f"/mnt/recordings/{helper.usb.RECORDINGS_FOLDER}", 0o775)


def test_recording_storage_repair_rejects_non_allowlisted_path():
    request = (
        b'{"operation":"recording_storage.repair_permissions","payload":'
        b'{"recordings_dir":"/etc","camera_id":"cam-x"}}'
    )
    with pytest.raises(helper.HelperRequestError, match="allowlisted"):
        helper.handle_request(request)


def test_recording_storage_repair_rejects_bad_camera_id():
    request = (
        b'{"operation":"recording_storage.repair_permissions","payload":'
        b'{"recordings_dir":"/mnt/recordings/home-monitor-recordings",'
        b'"camera_id":"../bad"}}'
    )
    with pytest.raises(helper.HelperRequestError, match="camera id"):
        helper.handle_request(request)


def test_recording_storage_repair_chowns_camera_tree():
    fake_pwd = MagicMock()
    fake_pwd.getpwnam.return_value.pw_uid = 996
    fake_grp = MagicMock()
    fake_grp.getgrnam.return_value.gr_gid = 994
    root = "/mnt/recordings/home-monitor-recordings/cam-x"

    with (
        patch.object(helper, "pwd", fake_pwd),
        patch.object(helper, "grp", fake_grp),
        patch.object(helper.os, "makedirs") as makedirs,
        patch.object(
            helper.os,
            "walk",
            return_value=[
                (root, ["2026-05-30"], [".segments.log"]),
                (f"{root}/2026-05-30", [], ["clip.mp4"]),
            ],
        ),
        patch.object(helper.os, "chown", create=True) as chown,
        patch.object(helper.os, "chmod") as chmod,
    ):
        data = helper.handle_request(
            b'{"operation":"recording_storage.repair_permissions","payload":'
            b'{"recordings_dir":"/mnt/recordings/home-monitor-recordings",'
            b'"camera_id":"cam-x"}}'
        )

    assert data == {"path": root}
    makedirs.assert_called_once_with(root, exist_ok=True)
    chown.assert_any_call(root, 996, 994)
    chown.assert_any_call(f"{root}/.segments.log", 996, 994)
    chown.assert_any_call(f"{root}/2026-05-30/clip.mp4", 996, 994)
    chmod.assert_any_call(root, 0o775)
    chmod.assert_any_call(f"{root}/.segments.log", 0o664)


def test_usb_unmount_and_format_delegate_to_usb_module():
    with patch.object(helper.usb, "unmount_device", return_value=(True, "")) as unmount:
        assert helper.handle_request(b'{"operation":"usb.unmount","payload":{}}') == {}
    unmount.assert_called_once_with(helper.usb.DEFAULT_MOUNT_POINT)

    with (
        patch.object(
            helper.usb, "detect_devices", return_value=[{"path": "/dev/sda1"}]
        ),
        patch.object(helper.usb, "format_device", return_value=(True, "")) as fmt,
    ):
        assert (
            helper.handle_request(
                b'{"operation":"usb.format","payload":'
                b'{"device_path":"/dev/sda1","label":"Archive_1"}}'
            )
            == {}
        )
    fmt.assert_called_once_with("/dev/sda1", fstype="ext4", label="Archive_1")


def test_usb_format_rejects_bad_label():
    request = (
        b'{"operation":"usb.format","payload":'
        b'{"device_path":"/dev/sda1","label":"bad/label"}}'
    )
    with pytest.raises(helper.HelperRequestError, match="label"):
        helper.handle_request(request)


def test_usb_operations_surface_delegate_errors():
    with patch.object(helper.usb, "unmount_device", return_value=(False, "busy")):
        with pytest.raises(helper.HelperRequestError, match="busy"):
            helper.handle_request(b'{"operation":"usb.unmount","payload":{}}')
    with (
        patch.object(
            helper.usb,
            "detect_devices",
            return_value=[{"path": "/dev/sda1"}],
        ),
        patch.object(helper.usb, "mount_device", return_value=(False, "")),
    ):
        with pytest.raises(helper.HelperRequestError, match="mount failed"):
            helper.handle_request(
                b'{"operation":"usb.mount","payload":{"device_path":"/dev/sda1"}}'
            )
    with (
        patch.object(
            helper.usb,
            "detect_devices",
            return_value=[{"path": "/dev/sda1"}],
        ),
        patch.object(helper.usb, "format_device", return_value=(False, "")),
    ):
        with pytest.raises(helper.HelperRequestError, match="format failed"):
            helper.handle_request(
                b'{"operation":"usb.format","payload":{"device_path":"/dev/sda1"}}'
            )


def test_hostname_validation_blocks_shell_tokens():
    request = b'{"operation":"hostname.set","payload":{"hostname":"rpi;reboot"}}'
    with pytest.raises(helper.HelperRequestError, match="hostname"):
        helper.handle_request(request)


def test_hostname_set_builds_allowlisted_command():
    with patch.object(helper, "_run_command", return_value={"returncode": 0}) as run:
        data = helper.handle_request(
            b'{"operation":"hostname.set","payload":{"hostname":"rpi-divinu"}}'
        )

    assert data == {"returncode": 0}
    run.assert_called_once_with(
        ["hostnamectl", "set-hostname", "rpi-divinu"], timeout=10
    )


@pytest.mark.parametrize(
    ("operation", "payload", "expected"),
    [
        (
            "time.set_timezone",
            {"timezone": "Europe/Dublin"},
            ["timedatectl", "set-timezone", "Europe/Dublin"],
        ),
        ("time.set_ntp", {"enabled": True}, ["timedatectl", "set-ntp", "true"]),
        (
            "time.set_manual",
            {"stamp": "2026-05-12 10:30:00"},
            ["timedatectl", "set-time", "2026-05-12 10:30:00"],
        ),
        (
            "time.restart_timesyncd",
            {},
            ["systemctl", "restart", "systemd-timesyncd"],
        ),
    ],
)
def test_time_operations_build_allowlisted_commands(operation, payload, expected):
    request = {"operation": operation, "payload": payload}
    with patch.object(helper, "_run_command", return_value={"returncode": 0}) as run:
        assert helper.handle_request(helper.json.dumps(request).encode("utf-8")) == {
            "returncode": 0
        }

    run.assert_called_once()
    assert run.call_args.args[0] == expected


@pytest.mark.parametrize(
    ("operation", "payload", "message"),
    [
        ("time.set_timezone", {"timezone": "bad zone"}, "timezone"),
        ("time.set_manual", {"stamp": "now"}, "manual time"),
    ],
)
def test_time_operations_reject_invalid_values(operation, payload, message):
    request = {"operation": operation, "payload": payload}
    with pytest.raises(helper.HelperRequestError, match=message):
        helper.handle_request(helper.json.dumps(request).encode("utf-8"))


def test_wifi_operations_build_allowlisted_commands():
    with patch.object(helper, "_run_command", return_value={"returncode": 0}) as run:
        helper.handle_request(
            b'{"operation":"network.connect_wifi","payload":'
            b'{"ssid":"MysticNet","password":"passphrase"}}'
        )
        helper.handle_request(
            b'{"operation":"hotspot.connect_wifi","payload":'
            b'{"ssid":"MysticNet","password":"passphrase"}}'
        )
        helper.handle_request(b'{"operation":"hotspot.stop","payload":{}}')
        helper.handle_request(b'{"operation":"hotspot.start","payload":{}}')

    assert run.call_args_list[0].args[0] == [
        "nmcli",
        "device",
        "wifi",
        "connect",
        "MysticNet",
        "password",
        "passphrase",
        "ifname",
        "wlan0",
    ]
    assert run.call_args_list[1].args[0] == [
        "/opt/monitor/scripts/monitor-hotspot.sh",
        "connect",
        "MysticNet",
        "passphrase",
    ]
    assert run.call_args_list[2].args[0] == [
        "/opt/monitor/scripts/monitor-hotspot.sh",
        "stop",
    ]
    assert run.call_args_list[3].args[0] == [
        "systemctl",
        "restart",
        "monitor-hotspot.service",
    ]


def test_tailscale_up_builds_allowlisted_args():
    with patch.object(helper, "_run_command", return_value={"returncode": 0}) as run:
        data = helper.handle_request(
            b'{"operation":"tailscale.up","payload":'
            b'{"accept_routes":true,"ssh":true,"authkey":"tskey-test"}}'
        )

    assert data == {"returncode": 0}
    run.assert_called_once_with(
        [
            "tailscale",
            "up",
            "--timeout=5s",
            "--accept-routes",
            "--ssh",
            "--authkey=tskey-test",
        ],
        timeout=15,
        nonzero_ok=True,
    )


def test_tailscale_up_rejects_bad_authkey():
    request = b'{"operation":"tailscale.up","payload":{"authkey":"tskey with spaces"}}'
    with pytest.raises(helper.HelperRequestError, match="auth key"):
        helper.handle_request(request)


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("tailscale.down", ["tailscale", "down"]),
        ("tailscale.enable", ["systemctl", "enable", "--now", "tailscaled"]),
        ("tailscale.disable", ["systemctl", "disable", "--now", "tailscaled"]),
        ("system.reboot", ["systemctl", "reboot"]),
    ],
)
def test_simple_operations_build_allowlisted_commands(operation, expected):
    request = {"operation": operation, "payload": {}}
    with patch.object(helper, "_run_command", return_value={"returncode": 0}) as run:
        assert helper.handle_request(helper.json.dumps(request).encode("utf-8")) == {
            "returncode": 0
        }

    assert run.call_args.args[0] == expected


def test_ota_install_rejects_non_data_path():
    request = b'{"operation":"ota.install","payload":{"bundle_path":"/tmp/update.swu"}}'
    with pytest.raises(helper.HelperRequestError, match="OTA bundle"):
        helper.handle_request(request)


def test_ota_install_rejects_relative_bundle_path():
    request = b'{"operation":"ota.install","payload":{"bundle_path":"update.swu"}}'
    with pytest.raises(helper.HelperRequestError, match="OTA bundle"):
        helper.handle_request(request)


def test_ota_bundle_path_uses_posix_resolution_branch(monkeypatch):
    class FakePath:
        def __init__(self, value):
            self.value = value

        def resolve(self, *, strict=False):
            return self

        @property
        def suffix(self):
            return ".txt"

        def __str__(self):
            return self.value

    monkeypatch.setattr(helper.os, "name", "posix", raising=False)
    monkeypatch.setattr(helper, "Path", FakePath)

    with pytest.raises(helper.HelperRequestError, match="OTA bundle"):
        helper._validate_ota_bundle_path("/data/ota/update.txt")


def test_ota_verify_rejects_non_allowlisted_public_key():
    request = (
        b'{"operation":"ota.verify","payload":'
        b'{"bundle_path":"/data/ota/update.swu","public_key_path":"/data/ota/dev.crt"}}'
    )
    with pytest.raises(helper.HelperRequestError, match="public key"):
        helper.handle_request(request)


def test_ota_verify_without_public_key_omits_key_argument():
    request = (
        b'{"operation":"ota.verify","payload":{"bundle_path":"/data/ota/update.swu"}}'
    )
    with (
        patch.object(helper, "_swupdate_env", return_value={"TMPDIR": "/data/ota/tmp"}),
        patch.object(helper, "_run_command", return_value={"returncode": 0}) as run,
    ):
        data = helper.handle_request(request)

    assert data == {"returncode": 0}
    run.assert_called_once_with(
        ["swupdate", "-c", "-i", "/data/ota/update.swu"],
        timeout=60,
        nonzero_ok=True,
        env={"TMPDIR": "/data/ota/tmp"},
    )


def test_ota_verify_accepts_production_public_key():
    request = (
        b'{"operation":"ota.verify","payload":'
        b'{"bundle_path":"/data/ota/update.swu",'
        b'"public_key_path":"/etc/swupdate-public.crt"}}'
    )
    with (
        patch.object(helper, "_swupdate_env", return_value={"TMPDIR": "/data/ota/tmp"}),
        patch.object(helper, "_run_command", return_value={"returncode": 0}) as run,
    ):
        data = helper.handle_request(request)

    assert data == {"returncode": 0}
    run.assert_called_once_with(
        [
            "swupdate",
            "-c",
            "-i",
            "/data/ota/update.swu",
            "-k",
            "/etc/swupdate-public.crt",
        ],
        timeout=60,
        nonzero_ok=True,
        env={"TMPDIR": "/data/ota/tmp"},
    )


def test_ota_install_accepts_production_public_key():
    request = (
        b'{"operation":"ota.install","payload":'
        b'{"bundle_path":"/data/ota/update.swu",'
        b'"public_key_path":"/etc/swupdate-public.crt"}}'
    )
    with (
        patch.object(helper, "_swupdate_env", return_value={"TMPDIR": "/data/ota/tmp"}),
        patch.object(helper, "_run_command", return_value={"returncode": 0}) as run,
    ):
        data = helper.handle_request(request)

    assert data == {"returncode": 0}
    run.assert_called_once_with(
        [
            "swupdate",
            "-i",
            "/data/ota/update.swu",
            "-k",
            "/etc/swupdate-public.crt",
        ],
        timeout=600,
        nonzero_ok=True,
        env={"TMPDIR": "/data/ota/tmp"},
    )


def test_swupdate_env_keeps_root_created_tmp_writable_by_monitor(monkeypatch):
    fake_pwd = MagicMock()
    fake_pwd.getpwnam.return_value.pw_uid = 996
    fake_grp = MagicMock()
    fake_grp.getgrnam.return_value.gr_gid = 994
    monkeypatch.setattr(helper, "pwd", fake_pwd)
    monkeypatch.setattr(helper, "grp", fake_grp)
    monkeypatch.setattr(helper, "OTA_DIR", "/data/ota")

    with (
        patch.object(helper.os, "makedirs") as makedirs,
        patch.object(helper.os, "chown", create=True) as chown,
        patch.object(helper.os, "chmod") as chmod,
    ):
        env = helper._swupdate_env()

    assert env["TMPDIR"] == "/data/ota/tmp"
    makedirs.assert_any_call("/data/ota", exist_ok=True)
    makedirs.assert_any_call("/data/ota/tmp", exist_ok=True)
    chown.assert_any_call("/data/ota", 996, 994)
    chown.assert_any_call("/data/ota/tmp", 996, 994)
    chmod.assert_any_call("/data/ota", 0o755)
    chmod.assert_any_call("/data/ota/tmp", 0o755)


def test_ota_repair_storage_repairs_allowlisted_tree(monkeypatch):
    fake_pwd = MagicMock()
    fake_pwd.getpwnam.return_value.pw_uid = 996
    fake_grp = MagicMock()
    fake_grp.getgrnam.return_value.gr_gid = 994
    monkeypatch.setattr(helper, "pwd", fake_pwd)
    monkeypatch.setattr(helper, "grp", fake_grp)
    monkeypatch.setattr(helper, "OTA_DIR", "/data/ota")

    with (
        patch.object(helper.os, "makedirs") as makedirs,
        patch.object(
            helper.os,
            "walk",
            return_value=[
                ("/data/ota", ["inbox", "linked"], ["server.swu", "linked.swu"]),
                ("/data/ota/inbox", [], ["camera.swu"]),
            ],
        ),
        patch.object(
            helper.os.path,
            "islink",
            side_effect=lambda path: (
                str(path).endswith("linked") or str(path).endswith("linked.swu")
            ),
        ),
        patch.object(helper.os, "chown", create=True) as chown,
        patch.object(helper.os, "chmod") as chmod,
    ):
        data = helper.handle_request(b'{"operation":"ota.repair_storage","payload":{}}')

    assert data == {"path": "/data/ota"}
    makedirs.assert_any_call("/data/ota/inbox", exist_ok=True)
    makedirs.assert_any_call("/data/ota/staging", exist_ok=True)
    makedirs.assert_any_call("/data/ota/tmp", exist_ok=True)
    makedirs.assert_any_call("/data/ota/camera-library", exist_ok=True)
    chown.assert_any_call("/data/ota", 996, 994)
    chown.assert_any_call("/data/ota/inbox/camera.swu", 996, 994)
    chmod.assert_any_call("/data/ota", 0o755)
    chmod.assert_any_call("/data/ota/server.swu", 0o644)


def test_ota_repair_storage_rejects_walk_escape(monkeypatch):
    fake_pwd = MagicMock()
    fake_pwd.getpwnam.return_value.pw_uid = 996
    fake_grp = MagicMock()
    fake_grp.getgrnam.return_value.gr_gid = 994
    monkeypatch.setattr(helper, "pwd", fake_pwd)
    monkeypatch.setattr(helper, "grp", fake_grp)
    monkeypatch.setattr(helper, "OTA_DIR", "/data/ota")

    with (
        patch.object(helper.os, "makedirs"),
        patch.object(helper.os, "walk", return_value=[("/etc", [], [])]),
        pytest.raises(helper.HelperRequestError, match="escaped allowlist"),
    ):
        helper.handle_request(b'{"operation":"ota.repair_storage","payload":{}}')


def test_ota_repair_storage_reports_user_lookup_failure(monkeypatch):
    fake_pwd = MagicMock()
    fake_pwd.getpwnam.side_effect = KeyError("monitor")
    fake_grp = MagicMock()
    monkeypatch.setattr(helper, "pwd", fake_pwd)
    monkeypatch.setattr(helper, "grp", fake_grp)

    with pytest.raises(helper.HelperRequestError, match="permission repair failed"):
        helper.handle_request(b'{"operation":"ota.repair_storage","payload":{}}')


def test_led_set_builds_allowlisted_command_with_flags():
    with patch.object(helper, "_run_command", return_value={"returncode": 0}) as run:
        data = helper.handle_request(
            b'{"operation":"led.set","payload":'
            b'{"state":"ota_installing","role":"camera",'
            b'"force":true,"init":true,"clear_activation":true}}'
        )

    assert data == {"returncode": 0}
    run.assert_called_once_with(
        [
            helper.LEDCTL,
            "ota-installing",
            "--role",
            "camera",
            "--force",
            "--init",
            "--clear-activation",
        ],
        timeout=10,
        nonzero_ok=True,
    )


def test_led_set_rejects_unlisted_state_and_role():
    with pytest.raises(helper.HelperRequestError, match="LED state"):
        helper.handle_request(
            b'{"operation":"led.set","payload":{"state":"blink-root"}}'
        )
    with pytest.raises(helper.HelperRequestError, match="LED role"):
        helper.handle_request(
            b'{"operation":"led.set","payload":{"state":"healthy","role":"root"}}'
        )


def test_set_socket_permissions_tolerates_missing_monitor_group():
    fake_grp = MagicMock()
    fake_grp.getgrnam.side_effect = KeyError("monitor")

    with (
        patch.object(helper, "grp", fake_grp),
        patch.object(helper.os, "chmod") as chmod,
        patch.object(helper.os, "chown", create=True) as chown,
    ):
        helper._set_socket_permissions("/run/monitor/helper.sock")

    chmod.assert_called_once_with("/run/monitor/helper.sock", 0o660)
    chown.assert_not_called()


def test_run_command_error_paths():
    with patch.object(helper.subprocess, "run", side_effect=FileNotFoundError):
        with pytest.raises(helper.HelperRequestError, match="missing not found"):
            helper._run_command(["missing"])
    with patch.object(
        helper.subprocess,
        "run",
        side_effect=subprocess.TimeoutExpired(["slow"], 1),
    ):
        with pytest.raises(helper.HelperRequestError, match="slow timed out"):
            helper._run_command(["slow"])
    with patch.object(helper.subprocess, "run", side_effect=OSError("denied")):
        with pytest.raises(helper.HelperRequestError, match="denied"):
            helper._run_command(["cmd"])
    result = MagicMock(returncode=7, stdout="out", stderr="")
    with patch.object(helper.subprocess, "run", return_value=result):
        with pytest.raises(helper.HelperRequestError, match="out"):
            helper._run_command(["cmd"])
    with patch.object(helper.subprocess, "run", return_value=result):
        assert helper._run_command(["cmd"], nonzero_ok=True)["returncode"] == 7
