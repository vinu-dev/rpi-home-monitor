SUMMARY = "Video/camera packages for Zero 2W camera node"
DESCRIPTION = "FFmpeg, libcamera, and v4l for video capture and RTSP streaming. \
Includes numpy so the on-camera motion detector \
(docs/exec-plans/motion-detection.md) can run on-device."
LICENSE = "MIT"

inherit packagegroup

# python3-numpy lives in meta-python (already in bblayers.conf) — the
# MotionDetector's frame-diff math depends on it.
#
# Picamera2 + libcamera Python bindings are NOT in this image yet:
# - `python3-picamera2` is not packaged in meta-raspberrypi scarthgap.
#   It needs either a custom recipe here (future work) or an override
#   branch. Adding it blindly fails parse with "Nothing RPROVIDES".
# - The libcamera Python bindings exist as the `libcamera-pycamera`
#   subpackage but are disabled via `PACKAGECONFIG[pycamera] = ...
#   -Dpycamera=disabled` by default in the scarthgap recipe. Enabling
#   them requires a `libcamera_%.bbappend` that flips the PACKAGECONFIG.
#
# Until those two land, Phase 2 of motion-detection will use a
# ffmpeg-tee scheme: the existing libcamera-vid → ffmpeg pipeline adds
# a second output (downsampled YUV) via the `tee` muxer, which a Python
# thread reads and feeds the MotionDetector. No picamera2 needed.
RDEPENDS:${PN} = " \
    ffmpeg \
    v4l-utils \
    libcamera \
    libcamera-apps \
    python3-numpy \
    "
