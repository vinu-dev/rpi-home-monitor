# REQ: SWR-046, SWR-072; RISK: RISK-019; SEC: SC-018; TEST: TC-043, TC-045
# Yocto recipe for boto3 — Amazon AWS SDK for Python.
#
# Vendored in meta-home-monitor because meta-openembedded's
# meta-python removed python3-boto3 in commit `bc98fb0765` (Oct
# 2023). The server image gained boto3 as a runtime dep in 1.5.0
# with the offsite-backup feature (#243), but the Yocto recipe was
# not added at that time, which broke the server-prod image build
# (`Nothing RPROVIDES 'python3-boto3'`).
#
# Version pin: 1.34.162 is the latest 1.34.x patch — matches the
# `boto3>=1.34` constraint in `app/server/setup.py`. The transitive
# botocore + s3transfer recipes are pinned to compatible versions
# in their own recipes (1.34.162 and 0.10.2 respectively).
SUMMARY = "The AWS SDK for Python"
DESCRIPTION = "Boto3 is the Amazon Web Services (AWS) Software Development Kit (SDK) for Python, which allows Python developers to write software that makes use of services like Amazon S3 and Amazon EC2."
HOMEPAGE = "https://github.com/boto/boto3"
SECTION = "devel/python"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://LICENSE;md5=2ee41112a44fe7014dce33e26468ba93"

SRC_URI[sha256sum] = "873f8f5d2f6f85f1018cbb0535b03cceddc7b655b61f66a0a56995238804f41f"

inherit pypi setuptools3

RDEPENDS:${PN} += " \
    python3-botocore \
    python3-jmespath \
    python3-s3transfer \
"

BBCLASSEXTEND = "native nativesdk"
