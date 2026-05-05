# REQ: SWR-046, SWR-072; RISK: RISK-019; SEC: SC-018; TEST: TC-043, TC-045
# Yocto recipe for PyOTP — Python One Time Password Library.
#
# Vendored in meta-home-monitor because pyotp is not provided by
# meta-openembedded's meta-python layer. The server image gained a
# runtime dependency on pyotp in 1.5.0 with the TOTP-2FA work (#238)
# but the Yocto recipe was not added at that time, which broke the
# server-prod image build (`Nothing RPROVIDES 'python3-pyotp'`).
#
# Version pin matches `app/server/setup.py`:
#     pyotp>=2.9
SUMMARY = "Python One Time Password Library"
DESCRIPTION = "PyOTP is a Python library for generating and verifying one-time passwords. It can be used to implement two-factor (2FA) or multi-factor (MFA) authentication methods in web applications and in other systems that require users to log in."
HOMEPAGE = "https://github.com/pyauth/pyotp"
SECTION = "devel/python"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=f4889ab24aecac0a410d83c0323f9daf"

SRC_URI[sha256sum] = "f3b21d5994ba2acde054a443bd5e2d384175449c7d2b6d1a0614dbca3a63abfc"

inherit pypi setuptools3

RDEPENDS:${PN} += " \
    python3-hashlib \
    python3-hmac \
    python3-json \
    python3-logging \
    python3-math \
"

BBCLASSEXTEND = "native nativesdk"
