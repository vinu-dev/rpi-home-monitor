"""
Camera streamer entry point.

Thin wrapper — loads config, detects platform, and delegates to
CameraLifecycle for the full startup/streaming/shutdown state machine.
"""

import logging
import signal

from camera_streamer.logging_config import configure_logging

configure_logging()
log = logging.getLogger("camera-streamer")

# Global shutdown flag
_shutdown = False


def _handle_signal(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global _shutdown
    log.info("Received signal %d, shutting down...", signum)
    _shutdown = True


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

    lifecycle = CameraLifecycle(
        config=config,
        platform=platform,
        shutdown_event=lambda: _shutdown,
    )

    try:
        lifecycle.run()
    except KeyboardInterrupt:
        lifecycle.shutdown()


if __name__ == "__main__":
    main()
