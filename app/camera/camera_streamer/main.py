"""
Camera streamer entry point.

Lifecycle:
1. Load config from /data/config/camera.conf
2. Start Avahi mDNS advertisement
3. Start ffmpeg RTSPS streaming to server
4. Start OTA agent (listen for update push)
5. Monitor stream health, auto-reconnect on failure
6. Run until stopped by systemd
"""
import sys
import signal
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("camera-streamer")


def main():
    """Entry point for camera-streamer service."""
    log.info("Camera streamer starting...")

    # TODO: Implement
    # 1. config = ConfigManager.load()
    # 2. discovery = DiscoveryService(config)
    # 3. stream = StreamManager(config)
    # 4. ota = OTAAgent(config)
    # 5. Start all, wait for signal

    log.info("Camera streamer stopped.")


if __name__ == "__main__":
    main()
