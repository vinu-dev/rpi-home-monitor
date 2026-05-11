# REQ: SWR-027, SWR-028, SWR-063; RISK: RISK-013, RISK-018; SEC: SC-013, SC-019; TEST: TC-024, TC-025, TC-044
"""Tests for the unprivileged-to-root helper client."""

from __future__ import annotations

from unittest.mock import patch

from monitor.services import privileged, usb


def test_should_use_helper_requires_service_environment(monkeypatch):
    monkeypatch.delenv("MONITOR_PRIVILEGED_HELPER_SOCKET", raising=False)
    monkeypatch.delenv("MONITOR_DISABLE_PRIVILEGED_HELPER", raising=False)

    assert privileged.should_use_helper() is False


def test_usb_mount_uses_helper_when_unprivileged():
    with (
        patch.object(privileged, "should_use_helper", return_value=True),
        patch.object(privileged, "request_result", return_value=(True, "")) as req,
    ):
        ok, err = usb.mount_device("/dev/sda1")

    assert ok is True
    assert err == ""
    req.assert_called_once_with(
        "usb.mount",
        {"device_path": "/dev/sda1", "mount_point": usb.DEFAULT_MOUNT_POINT},
    )


def test_usb_format_uses_helper_timeout_when_unprivileged():
    with (
        patch.object(privileged, "should_use_helper", return_value=True),
        patch.object(
            privileged, "request_result", return_value=(False, "denied")
        ) as req,
    ):
        ok, err = usb.format_device("/dev/sda1")

    assert ok is False
    assert err == "denied"
    req.assert_called_once_with(
        "usb.format",
        {"device_path": "/dev/sda1", "fstype": "ext4", "label": "HomeMonitor"},
        timeout=130,
    )
