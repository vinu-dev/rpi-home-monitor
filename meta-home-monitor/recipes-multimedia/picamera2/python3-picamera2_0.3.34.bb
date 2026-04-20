SUMMARY = "Picamera2 — next-generation Python library for Raspberry Pi cameras"
DESCRIPTION = "High-level Python wrapper over libcamera that exposes the \
Pi camera pipeline: dual-stream capture (main + lores) plus hardware \
H.264 encoding. For the Zero 2W camera node we use H264Encoder + \
FileOutput writing into an ffmpeg -c copy RTSPS pusher, with a lores \
YUV stream consumed by the motion detector. See ADR-0021 and \
docs/exec-plans/motion-detection.md."
HOMEPAGE = "https://github.com/raspberrypi/picamera2"
LICENSE = "BSD-2-Clause"
LIC_FILES_CHKSUM = "file://LICENSE;md5=6541a38108b5accb25bd55a14e76086d"

SRC_URI[sha256sum] = "fa923c2d25a124b1b591b332e10836a08690a1cf6bd233bb25862466d881229d"

# Patch upstream to lazy-import simplejpeg / piexif / pidng / PIL / videodev2
# / libav_* encoders so the camera-side install doesn't need any of them.
# Picamera2 upstream imports them all at package load (for JPEG snapshot,
# DNG capture, EXIF, videodev2 helpers, and software H.264 on non-VC4
# platforms) — we use H264Encoder (VC4 hardware path) + capture_array only.
# simplejpeg in particular bundles libjpeg-turbo + yasm sources that its
# setup.py downloads at build time, which bitbake's sandboxed do_compile
# can't do. Dropping the hard imports lets us ship a minimal dep chain.
SRC_URI += "file://0001-lazy-optional-imports.patch"

PYPI_PACKAGE = "picamera2"

inherit pypi python_setuptools_build_meta

# Minimal RDEPENDS. Upstream's install_requires lists av, PiDNG, piexif,
# pillow, simplejpeg, videodev2, python-prctl, libarchive-c, tqdm,
# jsonschema, OpenEXR — all lazy-imported on paths we don't hit after
# the 0001 patch. See the module comment in camera_streamer/picam_backend.py.
RDEPENDS:${PN} = " \
    libcamera-pycamera \
    python3-numpy \
    python3-prctl \
    python3-jsonschema \
"
