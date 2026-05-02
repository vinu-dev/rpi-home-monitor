# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Tests for main._handle_signal — SIGTERM must always produce process exit.

A hung ``lifecycle.shutdown()`` (ffmpeg wait, HTTPServer.shutdown blocking
on a slow client, etc.) must not be able to keep the process alive past
the graceful-shutdown budget, otherwise systemd never sees us exit and
never respawns us. The watchdog timer is the architectural guarantee
that SIGTERM always results in os._exit, even when cooperative teardown
has deadlocked — see the auto-unpair and re-pair flows in
heartbeat.py and status_server.py.
"""

from unittest.mock import patch

import camera_streamer.main as main_mod


class TestHandleSignal:
    def setup_method(self):
        # Reset module-level flag between tests
        main_mod._shutdown = False

    def test_first_signal_sets_flag_and_arms_watchdog(self):
        with patch("camera_streamer.main.threading.Timer") as mock_timer:
            main_mod._handle_signal(15, None)

            assert main_mod._shutdown is True
            # Watchdog Timer instantiated with the configured budget
            args, _ = mock_timer.call_args
            assert args[0] == main_mod._SHUTDOWN_WATCHDOG_SECONDS
            # The target is a force-exit callable (we check it by calling it
            # under a patched os._exit so we don't kill the pytest process)
            target = args[1]
            with patch("camera_streamer.main.os._exit") as mock_exit:
                target()
                mock_exit.assert_called_once_with(0)
            # Timer started as a daemon so it never blocks process exit
            instance = mock_timer.return_value
            assert instance.daemon is True
            instance.start.assert_called_once()

    def test_second_signal_forces_immediate_exit(self):
        main_mod._shutdown = True  # simulate first SIGTERM already handled
        with patch("camera_streamer.main.os._exit") as mock_exit:
            main_mod._handle_signal(15, None)
            mock_exit.assert_called_once_with(1)
