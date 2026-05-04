# REQ: SWR-062; RISK: RISK-001, RISK-008; TEST: TC-005, TC-047
"""Unit tests for the camera sd_notify helper."""

from unittest.mock import MagicMock, patch

from camera_streamer.sd_notify import WATCHDOG, notify


def test_notify_noops_when_notify_socket_unset():
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("camera_streamer.sd_notify.socket.socket") as mock_socket,
    ):
        notify(WATCHDOG)

    mock_socket.assert_not_called()


def test_notify_translates_abstract_socket_address():
    sock = MagicMock()
    with (
        patch.dict("os.environ", {"NOTIFY_SOCKET": "@/run/systemd/notify"}),
        patch("camera_streamer.sd_notify.socket.AF_UNIX", 1, create=True),
        patch("camera_streamer.sd_notify.socket.socket", return_value=sock),
    ):
        notify(WATCHDOG)

    sock.connect.assert_called_once_with("\0/run/systemd/notify")
    sock.sendall.assert_called_once_with(WATCHDOG)


def test_notify_suppresses_socket_errors(caplog):
    caplog.set_level("DEBUG")
    with (
        patch.dict("os.environ", {"NOTIFY_SOCKET": "/run/systemd/notify"}),
        patch("camera_streamer.sd_notify.socket.socket", side_effect=OSError("boom")),
    ):
        notify(WATCHDOG)

    assert "sd_notify send failed" in caplog.text
