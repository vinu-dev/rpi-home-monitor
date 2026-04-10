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


def _resolve_server(config):
    """Resolve server address — handles mDNS names like homemonitor.local."""
    import socket
    addr = config.server_ip
    if not addr:
        return
    try:
        ip = socket.gethostbyname(addr)
        log.info("Server address resolved: %s -> %s", addr, ip)
    except socket.gaierror:
        log.warning(
            "Cannot resolve server address '%s' — mDNS may not be ready yet. "
            "Will retry when streaming starts.", addr
        )


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

    # 2b. Resolve server address (mDNS: homemonitor.local → IP)
    if config.is_configured:
        _resolve_server(config)

    # 3. Validate camera device
    log.info("--- Camera Hardware Check ---")
    from camera_streamer.capture import CaptureManager
    capture = CaptureManager()
    if not capture.check():
        log.error(
            "Camera device not available. Troubleshooting:\n"
            "  1. Check ribbon cable is seated firmly (blue side to board)\n"
            "  2. Check config.txt has: start_x=1 and gpu_mem=128\n"
            "  3. For PiHut ZeroCam (OV5647): dtoverlay=ov5647\n"
            "  4. Run: vcgencmd get_camera\n"
            "  5. Run: ls -la /dev/video*\n"
            "  6. Run: dmesg | grep -i camera\n"
            "Will retry via health monitor..."
        )
    else:
        log.info("Camera hardware OK: device=%s h264=%s",
                 capture.device, capture.supports_h264())

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

    # LED: solid on = running
    from camera_streamer import led
    led.connected()
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
