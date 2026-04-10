"""
Camera streamer entry point.

Lifecycle:
1. Load config from /data/config/camera.conf
2. If not configured: start WiFi hotspot + setup HTTP server
3. Validate camera device (v4l2)
4. Start Avahi mDNS advertisement
5. Start ffmpeg RTSP streaming to server
6. Monitor stream health, auto-reconnect on failure
7. Run until stopped by systemd (SIGTERM)
"""
import sys
import signal
import logging
import time
import os

# LOG_LEVEL env controls verbosity:
# Dev builds set LOG_LEVEL=DEBUG via systemd drop-in
# Prod defaults to WARNING
_log_level = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.WARNING),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("camera-streamer")

# Global shutdown event
_shutdown = False


def _handle_signal(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global _shutdown
    log.info("Received signal %d, shutting down...", signum)
    _shutdown = True


def main():
    """Entry point for camera-streamer service."""
    global _shutdown

    log.info("Camera streamer starting (log_level=%s)", _log_level)

    # Register signal handlers
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # 1. Load configuration
    log.debug("Loading config...")
    from camera_streamer.config import ConfigManager
    config = ConfigManager()
    config.load()
    log.debug("Config loaded: data_dir=%s server_ip=%s camera_id=%s",
              config.data_dir, getattr(config, 'server_ip', 'N/A'), config.camera_id)

    # 2. Check if setup is needed (first boot)
    from camera_streamer.wifi_setup import WifiSetupServer
    setup_server = WifiSetupServer(config)
    if setup_server.needs_setup():
        log.info("First boot — starting setup wizard")
        setup_server.start()

        # Wait for setup to complete or shutdown
        while not _shutdown and setup_server.needs_setup():
            time.sleep(1)

        setup_server.stop()

        if _shutdown:
            log.info("Shutdown during setup")
            return

        # Reload config after setup
        config.load()
        log.info("Setup complete, continuing with startup")

    # 3. Validate camera device
    from camera_streamer.capture import CaptureManager
    capture = CaptureManager()
    if not capture.check():
        log.error("Camera device not available — will retry via health monitor")

    # 4. Start mDNS advertisement
    from camera_streamer.discovery import DiscoveryService
    discovery = DiscoveryService(config)
    discovery.start()

    # 5. Start streaming (if server is configured)
    from camera_streamer.stream import StreamManager
    stream = StreamManager(config)
    if config.is_configured:
        stream.start()
    else:
        log.warning("Server not configured — streaming disabled")

    # 6. Start health monitoring
    from camera_streamer.health import HealthMonitor
    health = HealthMonitor(config, capture, stream)
    health.start()

    log.info("Camera streamer running (camera=%s)", config.camera_id)

    # 7. Main loop — wait for shutdown
    try:
        while not _shutdown:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    # 8. Graceful shutdown
    log.info("Shutting down...")
    health.stop()
    stream.stop()
    discovery.stop()

    log.info("Camera streamer stopped.")


if __name__ == "__main__":
    main()
