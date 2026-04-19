SUMMARY = "simplejpeg — fast JPEG encoding / decoding using libturbojpeg"
DESCRIPTION = "Thin Python wrapper over libjpeg-turbo for fast JPEG IO. \
Hard dependency of picamera2 — imported at module load time for the \
JPEG snapshot path. No pillow dependency."
HOMEPAGE = "https://gitlab.com/jfolz/simplejpeg"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=af48f310216df53bb63b2f37290a00db"

# 1.8.2 is the last release compatible with numpy <2 (scarthgap pins
# python3-numpy 1.26.4). Bumping to 1.9.x pulls numpy 2.x ABI.
SRC_URI[sha256sum] = "b06e253a896c7fc4f257e11baf96d783817cea41360d0962a70c2743ba57bc30"

PYPI_PACKAGE = "simplejpeg"

inherit pypi python_setuptools_build_meta

DEPENDS += "jpeg cython-native"

RDEPENDS:${PN} = "python3-numpy libjpeg-turbo"
