# REQ: SWR-027, SWR-028, SWR-063; RISK: RISK-013, RISK-018; SEC: SC-013, SC-019; TEST: TC-024, TC-025, TC-044
"""Tests for the monitor privileged helper allowlist."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from monitor.services import privileged_helper as helper


def test_unknown_operation_is_rejected():
    with pytest.raises(helper.HelperRequestError, match="allowlisted"):
        helper.handle_request(b'{"operation":"shell.exec","payload":{}}')


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
    ):
        data = helper.handle_request(
            b'{"operation":"usb.mount","payload":{"device_path":"/dev/sda1"}}'
        )

    assert data == {}
    mount.assert_called_once_with("/dev/sda1", helper.usb.DEFAULT_MOUNT_POINT)


def test_hostname_validation_blocks_shell_tokens():
    request = b'{"operation":"hostname.set","payload":{"hostname":"rpi;reboot"}}'
    with pytest.raises(helper.HelperRequestError, match="hostname"):
        helper.handle_request(request)


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


def test_ota_install_rejects_non_data_path():
    request = b'{"operation":"ota.install","payload":{"bundle_path":"/tmp/update.swu"}}'
    with pytest.raises(helper.HelperRequestError, match="OTA bundle"):
        helper.handle_request(request)


def test_ota_verify_rejects_non_allowlisted_public_key():
    request = (
        b'{"operation":"ota.verify","payload":'
        b'{"bundle_path":"/data/ota/update.swu","public_key_path":"/data/ota/dev.crt"}}'
    )
    with pytest.raises(helper.HelperRequestError, match="public key"):
        helper.handle_request(request)


def test_ota_verify_accepts_production_public_key():
    request = (
        b'{"operation":"ota.verify","payload":'
        b'{"bundle_path":"/data/ota/update.swu",'
        b'"public_key_path":"/etc/swupdate-public.crt"}}'
    )
    with patch.object(helper, "_run_command", return_value={"returncode": 0}) as run:
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
    )
