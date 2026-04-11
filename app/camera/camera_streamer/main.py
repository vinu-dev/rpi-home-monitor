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


def _wait_for_wifi_connectivity(wifi_interface="wlan0", timeout=60):
    """Wait up to timeout seconds for WiFi interface to have an IP address.

    Args:
        wifi_interface: WiFi interface name (from Platform).
        timeout: Max seconds to wait.

    Returns True if connected, False if timed out.
    """
    import subprocess
    log.info("Checking WiFi connectivity on %s (timeout=%ds)...",
             wifi_interface, timeout)
    for elapsed in range(timeout):
        if _shutdown:
            return True  # Don't block shutdown
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show",
                 wifi_interface],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                if line.startswith("IP4.ADDRESS") and "/" in line:
                    ip = line.split(":", 1)[1].split("/")[0]
                    if ip and ip != "0.0.0.0":
                        log.info("WiFi connected with IP %s after %ds",
                                 ip, elapsed)
                        return True
        except Exception:
            pass
        time.sleep(1)
    log.warning("No WiFi IP on %s after %ds", wifi_interface, timeout)
    return False


def _revert_to_setup():
    """Remove setup stamp and restart service to trigger setup wizard."""
    import subprocess
    stamp = "/data/.setup-done"
    try:
        if os.path.isfile(stamp):
            os.remove(stamp)
            log.info("Removed %s — next boot will start setup wizard", stamp)
    except OSError as e:
        log.error("Failed to remove setup stamp: %s", e)

    log.info("Restarting camera-streamer service to enter setup mode...")
    try:
        subprocess.run(
            ["systemctl", "restart", "camera-streamer"],
            capture_output=True, timeout=10,
        )
    except Exception as e:
        log.error("Failed to restart service: %s", e)


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

    # 1b. Detect platform (hardware paths, WiFi interface, etc.)
    from camera_streamer.platform import Platform
    platform = Platform.detect()
    log.info("Platform: camera=%s wifi=%s led=%s thermal=%s hostname=%s",
             platform.camera_device, platform.wifi_interface,
             platform.led_path or "none", platform.thermal_path or "none",
             platform.hostname_prefix)

    # 1c. Configure LED controller for this platform
    from camera_streamer import led
    from camera_streamer.led import LedController
    led.set_controller(LedController(platform.led_path))

    # 2. Check if setup is needed (first boot)
    from camera_streamer.wifi_setup import WifiSetupServer
    setup_server = WifiSetupServer(
        config,
        wifi_interface=platform.wifi_interface,
        hostname_prefix=platform.hostname_prefix,
    )
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

    # 2a. Verify WiFi connectivity after setup (fallback hotspot)
    if not _wait_for_wifi_connectivity(platform.wifi_interface):
        log.error("WiFi has no IP after 60s — reverting to setup mode")
        _revert_to_setup()
        return

    # 2b. Resolve server address (mDNS: homemonitor.local → IP)
    if config.is_configured:
        _resolve_server(config)

    # 3. Validate camera device
    log.info("--- Camera Hardware Check ---")
    from camera_streamer.capture import CaptureManager
    capture = CaptureManager(device=platform.camera_device)
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
    stream = StreamManager(config, camera_device=platform.camera_device)
    if config.is_configured:
        stream.start()
    else:
        log.warning("Server not configured — streaming disabled")

    # 5b. Start status page HTTP server on port 80
    from camera_streamer.wifi_setup import CameraStatusServer
    status_server = CameraStatusServer(
        config, stream,
        wifi_interface=platform.wifi_interface,
        thermal_path=platform.thermal_path,
    )
    status_server.start()

    # 6. Start health monitoring
    from camera_streamer.health import HealthMonitor
    health = HealthMonitor(config, capture, stream,
                           thermal_path=platform.thermal_path)
    health.start()

    # LED: solid on = running
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
    status_server.stop()
    discovery.stop()

    log.info("Camera streamer stopped.")


if __name__ == "__main__":
    main()
