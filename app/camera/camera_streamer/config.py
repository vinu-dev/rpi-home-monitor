"""
Configuration manager.

Reads camera config from /data/config/camera.conf.
This file persists across OTA updates (on the data partition).

Config values:
  SERVER_IP      - RPi 4B server IP address
  SERVER_PORT    - RTSPS port (default: 8554)
  STREAM_NAME    - RTSP stream path
  WIDTH          - Video width (default: 1920)
  HEIGHT         - Video height (default: 1080)
  FPS            - Framerate (default: 25)
  CAMERA_ID      - Derived from hardware serial if not set
"""

# TODO: Implement ConfigManager class
