SUMMARY = "videodev2 — ctypes bindings for Linux V4L2 ioctl / constants"
DESCRIPTION = "Pure-Python ctypes wrapper over <linux/videodev2.h>. \
Required by picamera2's H264Encoder on the VC4 (Zero 2W) path — the \
V4L2_CID_MPEG_VIDEO_H264_* / V4L2_MPEG_VIDEO_H264_PROFILE_* constants \
come from this module at import time."
HOMEPAGE = "https://pypi.org/project/videodev2/"
# Upstream sdist 0.0.4 does not ship a separate LICENSE file, but PyPI
# classifier + setup.py declare BSD-2-Clause. Point LIC_FILES_CHKSUM at
# poky's bundled copy — an empty string fails populate_lic QA on
# scarthgap ("Recipe file fetches files and does not have license file
# information"). md5 matches poky/meta/files/common-licenses/BSD-2-Clause.
LICENSE = "BSD-2-Clause"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/BSD-2-Clause;md5=cb641bc04cda31daea161b1bc15da69f"

SRC_URI[sha256sum] = "c34ba70491d148c23a08cbacd8efabeb413cff5baa943a7548ac4abd1eb19e2a"

PYPI_PACKAGE = "videodev2"

inherit pypi python_setuptools_build_meta

RDEPENDS:${PN} = "python3-core"
