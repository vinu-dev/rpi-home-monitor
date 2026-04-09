"""
Video capture management.

Handles v4l2 device detection and configuration.
The actual capture is done by ffmpeg (started by StreamManager),
but this module validates the device exists and is accessible.

Checks:
- /dev/video0 exists
- v4l2 device supports h264 output
- Requested resolution is supported
"""

# TODO: Implement CaptureManager class
