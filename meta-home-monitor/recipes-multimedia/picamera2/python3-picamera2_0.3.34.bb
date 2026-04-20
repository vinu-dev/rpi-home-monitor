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
python do_unpack_append() {
    import os, re
    s = d.getVar('S')
    req_path = os.path.join(s, 'picamera2', 'request.py')
    enc_init = os.path.join(s, 'picamera2', 'encoders', '__init__.py')
    jpg_enc  = os.path.join(s, 'picamera2', 'encoders', 'jpeg_encoder.py')

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

    wrap(req_path, [
        ("import piexif",                       ["piexif"]),
        ("import simplejpeg",                   ["simplejpeg"]),
        ("from pidng.camdefs import Picamera2Camera", ["Picamera2Camera"]),
        ("from pidng.core import PICAM2DNG",    ["PICAM2DNG"]),
        ("from PIL import Image",               ["Image"]),
    ])
    wrap(enc_init, [
        ("import videodev2",                         ["videodev2"]),
        ("from .jpeg_encoder import JpegEncoder",    ["JpegEncoder"]),
        ("from .libav_h264_encoder import LibavH264Encoder", ["LibavH264Encoder"]),
        ("from .libav_mjpeg_encoder import LibavMjpegEncoder", ["LibavMjpegEncoder"]),
    ])
    wrap(jpg_enc, [
        ("import simplejpeg", ["simplejpeg"]),
    ])
}

# Minimal RDEPENDS — optional deps intentionally skipped (see above).
RDEPENDS:${PN} = " \
    libcamera-pycamera \
    python3-numpy \
    python3-prctl \
    python3-jsonschema \
"
