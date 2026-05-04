# REQ: SWR-012, SWR-037, SWR-062; RISK: RISK-001, RISK-008, RISK-022; TEST: TC-005, TC-035, TC-047
"""
Camera streamer entry point.

Thin wrapper — loads config, detects platform, and delegates to
CameraLifecycle for the full startup/streaming/shutdown state machine.
"""

import logging
import os
import signal
import threading

from camera_streamer.logging_config import configure_logging

configure_logging()
log = logging.getLogger("camera-streamer")

# Global shutdown flag
_shutdown = False
_watchdog_notifier = None

# Graceful-shutdown budget. If lifecycle.shutdown() does not return within
# this many seconds of receiving SIGTERM, we force the process to exit so
# systemd (Restart=always) respawns us cleanly. This matters because
# shutdown involves joining threads that wait on ffmpeg and on blocking
# socket I/O in HTTPServer.shutdown(); any one of those can block the
# main thread indefinitely and leave systemd thinking we are still healthy.
# 8s is comfortably longer than any well-behaved teardown path (ffmpeg
# SIGTERM → exit is ~1s, HTTPServer.shutdown ~0.5s) but short enough that
# the user sees the unit restart within a human-scale window.
_SHUTDOWN_WATCHDOG_SECONDS = 8.0


def _handle_signal(signum, frame):
    """Handle SIGTERM/SIGINT. Set graceful flag + arm forced-exit watchdog.

    Second signal = immediate force exit (user is impatient / systemd
    has escalated to SIGKILL pressure).
    """
    global _shutdown
    if _shutdown:
        log.warning("Second signal %d — forcing immediate exit", signum)
        os._exit(1)
    log.info(
        "Received signal %d, shutting down (watchdog %.1fs)...",
        signum,
        _SHUTDOWN_WATCHDOG_SECONDS,
    )
    _shutdown = True
    if _watchdog_notifier is not None:
        _watchdog_notifier.stop(stopping=True)

    def _force_exit():
        log.warning(
            "Graceful shutdown did not complete in %.1fs — forcing exit "
            "(systemd will respawn us).",
            _SHUTDOWN_WATCHDOG_SECONDS,
        )
        os._exit(0)

    t = threading.Timer(_SHUTDOWN_WATCHDOG_SECONDS, _force_exit)
    t.daemon = True
    t.start()


def main():
    """Entry point for camera-streamer service."""
    log.info("Camera streamer starting")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Load config
    from camera_streamer.config import ConfigManager

    config = ConfigManager()
    config.load()
    log.debug(
        "Config loaded: server_ip=%s camera_id=%s",
        getattr(config, "server_ip", "N/A"),
        config.camera_id,
    )

    # Detect platform
    from camera_streamer.platform import Platform

    platform = Platform.detect()

    # Run lifecycle state machine
    from camera_streamer.lifecycle import CameraLifecycle
    from camera_streamer.watchdog_notifier import WatchdogNotifier

    global _watchdog_notifier
    _watchdog_notifier = WatchdogNotifier()
    _watchdog_notifier.start()

    lifecycle = CameraLifecycle(
        config=config,
        platform=platform,
        shutdown_event=lambda: _shutdown,
        notifier=_watchdog_notifier,
    )

    try:
        lifecycle.run()
    except KeyboardInterrupt:
        lifecycle.shutdown()
    finally:
        if _watchdog_notifier is not None:
            _watchdog_notifier.stop(stopping=False)


if __name__ == "__main__":
    main()
