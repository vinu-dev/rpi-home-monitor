# REQ: SWR-062; RISK: RISK-001, RISK-008; TEST: TC-005, TC-047
"""Unit tests for the camera watchdog notifier."""

from unittest.mock import patch

from camera_streamer.sd_notify import READY, STOPPING, WATCHDOG
from camera_streamer.watchdog_notifier import WatchdogNotifier


def test_defaults_to_30_second_watchdog_interval(caplog):
    caplog.set_level("INFO")
    with patch.dict("os.environ", {"NOTIFY_SOCKET": "/run/systemd/notify"}, clear=True):
        notifier = WatchdogNotifier()

    assert notifier.watchdog_usec == 30_000_000
    assert notifier.interval_seconds == 10
    assert "WATCHDOG_USEC unset" in caplog.text


def test_mark_ready_and_stop_emit_notify_messages():
    messages = []
    with patch.dict(
        "os.environ",
        {
            "NOTIFY_SOCKET": "/run/systemd/notify",
            "WATCHDOG_USEC": "30000000",
        },
        clear=True,
    ):
        notifier = WatchdogNotifier(notify_func=messages.append)
        notifier.mark_ready()
        notifier.mark_ready()
        notifier.stop()

    assert messages == [READY, STOPPING]


def test_health_beats_do_not_mask_stale_lifecycle(caplog):
    messages = []
    now = [100.0]

    with patch.dict(
        "os.environ",
        {
            "NOTIFY_SOCKET": "/run/systemd/notify",
            "WATCHDOG_USEC": "30000000",
        },
        clear=True,
    ):
        notifier = WatchdogNotifier(clock=lambda: now[0], notify_func=messages.append)
        notifier.mark_ready()
        notifier.beat("health")
        notifier._tick()
        notifier.beat("lifecycle")
        notifier._tick()
        now[0] = 116.0
        caplog.set_level("INFO")
        notifier._tick()

    assert messages == [READY, WATCHDOG]
    assert "liveness gate withheld: lifecycle stale" in caplog.text


def test_start_logs_debug_when_notify_socket_unset(caplog):
    caplog.set_level("DEBUG")
    with patch.dict("os.environ", {}, clear=True):
        notifier = WatchdogNotifier()
        notifier.start()
        notifier.stop(stopping=False)

    assert "liveness disabled: NOTIFY_SOCKET unset" in caplog.text
