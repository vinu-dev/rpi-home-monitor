SUMMARY = "Picamera2 — next-generation Python library for Raspberry Pi cameras"
DESCRIPTION = "High-level Python wrapper over libcamera that exposes the \
Pi camera pipeline: dual-stream capture (main + lores), hardware H.264 \
encoding, and critically for us, motion-vector output from the H.264 \
encoder as a side-channel. See docs/exec-plans/motion-detection.md §D1."
HOMEPAGE = "https://github.com/raspberrypi/picamera2"
LICENSE = "BSD-2-Clause"
LIC_FILES_CHKSUM = "file://LICENSE;md5=6541a38108b5accb25bd55a14e76086d"

SRC_URI[sha256sum] = "fa923c2d25a124b1b591b332e10836a08690a1cf6bd233bb25862466d881229d"

PYPI_PACKAGE = "picamera2"

inherit pypi python_setuptools_build_meta

# picamera2's setup.py declares a maximalist install_requires list
# (av, PiDNG, piexif, pillow, simplejpeg, videodev2, python-prctl, av,
# libarchive-c, tqdm, jsonschema, OpenEXR). Most of these are imported
# lazily by specific submodules we don't use (DNG capture, EXR export,
# EXIF writing, PyAV-based recording, v4l2 helpers).
#
# We only touch Picamera2, H264Encoder, FileOutput, CircularOutput, and
# motion-vector output. Those need: libcamera-pycamera (system), numpy,
# pillow, simplejpeg, python-prctl, python-jsonschema. Not pulling the
# optional chain keeps the camera image size + build surface small.
#
# If a future call path imports one of the missing modules it'll raise
# at import time with a clear error rather than silently misbehave —
# safer than shipping everything "just in case".
RDEPENDS:${PN} = " \
    libcamera-pycamera \
    python3-numpy \
    python3-pillow \
    python3-simplejpeg \
    python3-prctl \
    python3-jsonschema \
    python3-libarchive-c \
    python3-tqdm \
"
