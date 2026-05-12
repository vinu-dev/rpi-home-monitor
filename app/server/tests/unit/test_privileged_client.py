# REQ: SWR-027, SWR-028, SWR-063; RISK: RISK-013, RISK-018; SEC: SC-013, SC-019; TEST: TC-024, TC-025, TC-044
"""Tests for the unprivileged-to-root helper client."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from monitor.services import privileged, usb


class FakeSocket:
    def __init__(self, chunks=None, error=None):
        self.chunks = list(chunks or [])
        self.error = error
        self.sent = b""
        self.timeout = None
        self.connected = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, socket_path):
        if self.error:
            raise self.error
        self.connected = socket_path

    def sendall(self, data):
        self.sent += data

    def recv(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


def _enable_unix_socket(monkeypatch):
    monkeypatch.setattr(privileged.socket, "AF_UNIX", 1, raising=False)


def test_should_use_helper_requires_service_environment(monkeypatch):
    monkeypatch.delenv("MONITOR_PRIVILEGED_HELPER_SOCKET", raising=False)
    monkeypatch.delenv("MONITOR_DISABLE_PRIVILEGED_HELPER", raising=False)

    assert privileged.should_use_helper() is False


def test_should_use_helper_respects_disable_flag(monkeypatch):
    monkeypatch.setenv("MONITOR_PRIVILEGED_HELPER_SOCKET", "/run/test.sock")
    monkeypatch.setenv("MONITOR_DISABLE_PRIVILEGED_HELPER", "1")

    assert privileged.should_use_helper() is False


def test_should_use_helper_detects_unprivileged_posix(monkeypatch):
    monkeypatch.setenv("MONITOR_PRIVILEGED_HELPER_SOCKET", "/run/test.sock")
    monkeypatch.delenv("MONITOR_DISABLE_PRIVILEGED_HELPER", raising=False)
    monkeypatch.setattr(privileged.os, "name", "posix", raising=False)
    monkeypatch.setattr(privileged.os, "geteuid", lambda: 1000, raising=False)

    assert privileged.should_use_helper() is True


def test_is_helper_available_requires_unix_socket_and_path(tmp_path, monkeypatch):
    _enable_unix_socket(monkeypatch)
    socket_path = tmp_path / "helper.sock"
    socket_path.write_text("", encoding="utf-8")

    assert privileged.is_helper_available(str(socket_path)) is True
    assert privileged.is_helper_available(str(tmp_path / "missing.sock")) is False


def test_request_success_round_trip(tmp_path, monkeypatch):
    _enable_unix_socket(monkeypatch)
    fake = FakeSocket([json.dumps({"ok": True, "data": {"done": True}}).encode()])
    socket_path = str(tmp_path / "helper.sock")
    (tmp_path / "helper.sock").write_text("", encoding="utf-8")

    with patch.object(privileged.socket, "socket", return_value=fake):
        data = privileged.request(
            "system.reboot",
            {"now": True},
            socket_path=socket_path,
            timeout=3,
        )

    assert data == {"done": True}
    assert fake.timeout == 3
    assert fake.connected == socket_path
    assert fake.sent == b'{"operation":"system.reboot","payload":{"now":true}}\n'


def test_request_rejects_unavailable_helper(tmp_path):
    with pytest.raises(privileged.PrivilegedHelperError, match="unavailable"):
        privileged.request("system.reboot", socket_path=str(tmp_path / "missing.sock"))


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        ([b"not json"], "invalid privileged helper response"),
        ([json.dumps({"ok": False, "error": "denied"}).encode()], "denied"),
        ([json.dumps({"ok": True, "data": []}).encode()], ""),
    ],
)
def test_request_response_error_paths(tmp_path, chunks, message, monkeypatch):
    _enable_unix_socket(monkeypatch)
    socket_path = str(tmp_path / "helper.sock")
    (tmp_path / "helper.sock").write_text("", encoding="utf-8")

    with patch.object(privileged.socket, "socket", return_value=FakeSocket(chunks)):
        if message:
            with pytest.raises(privileged.PrivilegedHelperError, match=message):
                privileged.request("system.reboot", socket_path=socket_path)
        else:
            assert privileged.request("system.reboot", socket_path=socket_path) == {}


def test_request_wraps_socket_errors(tmp_path, monkeypatch):
    _enable_unix_socket(monkeypatch)
    socket_path = str(tmp_path / "helper.sock")
    (tmp_path / "helper.sock").write_text("", encoding="utf-8")

    with patch.object(
        privileged.socket,
        "socket",
        return_value=FakeSocket(error=OSError("connect failed")),
    ):
        with pytest.raises(privileged.PrivilegedHelperError, match="connect failed"):
            privileged.request("system.reboot", socket_path=socket_path)


def test_request_result_reports_helper_error(tmp_path):
    with patch.object(
        privileged,
        "request",
        side_effect=privileged.PrivilegedHelperError("denied"),
    ):
        assert privileged.request_result("system.reboot") == (False, "denied")


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
