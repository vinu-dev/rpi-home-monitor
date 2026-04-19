SUMMARY = "Video/camera packages for Zero 2W camera node"
DESCRIPTION = "FFmpeg, libcamera + Python bindings, Picamera2, and numpy \
for dual-stream video capture and on-device motion detection via the \
H.264 encoder's motion-vector output (docs/exec-plans/motion-detection.md \
§D1, Phase 2). libcamera's PACKAGECONFIG[pycamera] is enabled via a \
meta-home-monitor bbappend so the pycamera Python bindings get built."
LICENSE = "MIT"

inherit packagegroup

# Motion detection pipeline:
#   libcamera (C++) ─► Picamera2 (Python) ─► H.264 encoder (hardware)
#                                              │
#                                              ├─► main H.264 stream   ─► ffmpeg RTSPS push
#                                              └─► motion_output       ─► per-frame MV blocks
#                                                                         (Python state machine)
#
# Dependency chain brought in via this packagegroup:
#   - ffmpeg / v4l-utils               — existing RTSP push pipeline.
#   - libcamera + libcamera-apps       — camera pipeline (unchanged).
#   - libcamera-pycamera               — Python bindings to libcamera,
#                                        enabled by our libcamera_%.bbappend.
#   - python3-picamera2                — Picamera2 high-level API.
#   - python3-numpy                    — motion state machine does
#                                        vector magnitude aggregation.
#   - python3-simplejpeg / pillow      — picamera2 snapshot paths.
RDEPENDS:${PN} = " \
    ffmpeg \
    v4l-utils \
    libcamera \
    libcamera-apps \
    libcamera-pycamera \
    python3-picamera2 \
    python3-numpy \
    python3-simplejpeg \
    python3-pillow \
    "
