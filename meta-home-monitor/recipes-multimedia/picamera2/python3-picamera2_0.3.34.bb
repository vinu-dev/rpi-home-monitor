# REQ: SWR-006, SWR-012, SWR-053; RISK: RISK-001, RISK-007; SEC: SC-004; TEST: TC-001, TC-012, TC-047
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

PYPI_PACKAGE = "picamera2"

inherit pypi python_setuptools_build_meta

# --- Lazy-load optional deps ---------------------------------------------
# Upstream picamera2 eager-imports simplejpeg / piexif / pidng / PIL /
# videodev2 / PyAV / libav encoders at package load for code paths we
# never touch (JPEG snapshots, DNG capture, software H.264 fallback for
# non-VC4 platforms). simplejpeg in particular bundles libjpeg-turbo +
# yasm source in its setup.py and downloads them at build time, which
# bitbake's sandboxed do_compile can't do.
#
# Wrap every such import in try/except via sed after unpack. The
# symbols collapse to None if the module is absent — calling the
# features will AttributeError, but our motion pipeline never does.
# We use capture_array + H264Encoder + FileOutput only.
python do_unpack:append() {
    import os
    s = d.getVar('S')

    def wrap(path, mappings):
        """Replace each raw import line with a try/except that soft-sets
        the bound names to None on ImportError."""
        with open(path) as f:
            src = f.read()
        for raw, names in mappings:
            replacement = (
                "try:\n"
                f"    {raw}\n"
                "except ImportError:\n"
                + "".join(f"    {n} = None\n" for n in names)
            )
            if raw not in src:
                bb.fatal(f"lazy-import sed: expected {raw!r} in {path}")
            src = src.replace(raw, replacement, 1)
        with open(path, 'w') as f:
            f.write(src)

    # request.py — JPEG / DNG / EXIF helpers we don't use.
    wrap(os.path.join(s, 'picamera2', 'request.py'), [
        ("import piexif",                               ["piexif"]),
        ("import simplejpeg",                           ["simplejpeg"]),
        ("from pidng.camdefs import Picamera2Camera",   ["Picamera2Camera"]),
        ("from pidng.core import PICAM2DNG",            ["PICAM2DNG"]),
    ])
    # encoders/__init__.py — software encoders (PyAV) + JpegEncoder.
    # NOTE: videodev2 is NOT wrapped here — H264Encoder hard-requires
    # its V4L2_* constants. videodev2 is in RDEPENDS.
    wrap(os.path.join(s, 'picamera2', 'encoders', '__init__.py'), [
        ("from .jpeg_encoder import JpegEncoder",               ["JpegEncoder"]),
        ("from .libav_h264_encoder import LibavH264Encoder",    ["LibavH264Encoder"]),
        ("from .libav_mjpeg_encoder import LibavMjpegEncoder",  ["LibavMjpegEncoder"]),
    ])
    wrap(os.path.join(s, 'picamera2', 'encoders', 'jpeg_encoder.py'), [
        ("import simplejpeg", ["simplejpeg"]),
    ])
    # outputs/__init__.py — FfmpegOutput + PyavOutput both need PyAV.
    wrap(os.path.join(s, 'picamera2', 'outputs', '__init__.py'), [
        ("from .ffmpegoutput import FfmpegOutput",  ["FfmpegOutput"]),
        ("from .pyavoutput import PyavOutput",      ["PyavOutput"]),
    ])
    # previews/__init__.py — GUI previews (DRM/Qt) we never run headless.
    wrap(os.path.join(s, 'picamera2', 'previews', '__init__.py'), [
        ("from .drm_preview import DrmPreview",                ["DrmPreview"]),
        ("from .qt_previews import QtGlPreview, QtPreview",    ["QtGlPreview", "QtPreview"]),
    ])
}

# Minimal RDEPENDS — optional deps intentionally skipped (see above).
RDEPENDS:${PN} = " \
    libcamera-pycamera \
    python3-numpy \
    python3-prctl \
    python3-jsonschema \
    python3-videodev2 \
    python3-pillow \
"
