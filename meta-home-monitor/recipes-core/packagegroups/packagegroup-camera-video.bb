SUMMARY = "Video/camera packages for Zero 2W camera node"
DESCRIPTION = "FFmpeg, libcamera, and v4l for video capture and RTSP streaming. \
Includes numpy + picamera2 so the on-camera motion detector and Picamera2 \
dual-stream pipeline (docs/exec-plans/motion-detection.md) can run on-device."
LICENSE = "MIT"

inherit packagegroup

# python3-numpy lives in meta-python (already in bblayers.conf).
#
# python3-libcamera and python3-picamera2 live in meta-raspberrypi
# (scarthgap); picamera2 requires the libcamera Python bindings, so
# both must be pulled in.
#
# Validation pending: the Yocto build VM has not been parse-checked
# against this addition yet (user asked not to disturb in-flight VM
# build). Before the next release, run on the VM:
#     bitbake -p
#     bitbake -e packagegroup-camera-video | grep ^RDEPENDS
# to confirm these names resolve in the scarthgap meta-raspberrypi
# layer revision pinned in bblayers.conf.
RDEPENDS:${PN} = " \
    ffmpeg \
    v4l-utils \
    libcamera \
    libcamera-apps \
    python3-numpy \
    python3-libcamera \
    python3-picamera2 \
    "
