# REQ: SWR-063; RISK: RISK-001, RISK-018; SEC: SC-019; TEST: TC-005, TC-044, TC-047
"""Unit tests for the monitor watchdog notifier."""

import urllib.error
from unittest.mock import patch

from monitor.services.watchdog_notifier import READY, WATCHDOG, WatchdogNotifier


class _Response:
    def __init__(self, status=200, body=b"ok\n"):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_first_success_sends_ready_then_watchdog():
    messages = []
    now = [100.0]

    with patch.dict(
        "os.environ",
        {
            "NOTIFY_SOCKET": "/run/systemd/notify",
            "WATCHDOG_USEC": "60000000",
        },
        clear=True,
    ):
        notifier = WatchdogNotifier(
            "http://127.0.0.1:5000/healthz",
            clock=lambda: now[0],
            urlopen=lambda request, timeout: _Response(),
        )
        notifier._notify = messages.append
        notifier._started_at = 0.0
        notifier._tick()
        notifier._tick()

    assert messages == [READY, WATCHDOG]


def test_probe_failure_withholds_watchdog_after_startup_grace(caplog):
    messages = []
    now = [40.0]

    def failing_urlopen(request, timeout):
        raise urllib.error.URLError("timeout")

    caplog.set_level("WARNING")
    with patch.dict(
        "os.environ",
        {
            "NOTIFY_SOCKET": "/run/systemd/notify",
            "WATCHDOG_USEC": "60000000",
        },
        clear=True,
    ):
        notifier = WatchdogNotifier(
            "http://127.0.0.1:5000/healthz",
            clock=lambda: now[0],
            urlopen=failing_urlopen,
        )
        notifier._notify = messages.append
        notifier._started_at = 0.0
        notifier._tick()

    assert messages == []
    assert "liveness gate withheld: probe timeout" in caplog.text


def test_start_logs_when_notify_socket_unset(caplog):
    caplog.set_level("DEBUG")
    with patch.dict("os.environ", {}, clear=True):
        notifier = WatchdogNotifier("http://127.0.0.1:5000/healthz")
        notifier.start()
        notifier.stop(stopping=False)

    assert "liveness disabled: NOTIFY_SOCKET unset" in caplog.text
