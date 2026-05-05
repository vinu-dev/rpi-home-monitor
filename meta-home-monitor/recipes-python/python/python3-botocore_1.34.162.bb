# REQ: SWR-046, SWR-072; RISK: RISK-019; SEC: SC-018; TEST: TC-043, TC-045
# Yocto recipe for botocore — low-level interface to AWS services.
#
# Vendored in meta-home-monitor because meta-openembedded's
# meta-python removed python3-botocore in commit `bc98fb0765` (Oct
# 2023) on the rationale that boto3/botocore should ship from
# meta-aws. We don't pull meta-aws (single-recipe layer dependency
# isn't worth it for two recipes), so we vendor here. The server
# image's offsite-backup feature (#243) needs boto3, which transitively
# needs botocore.
#
# Version pin: 1.34.162 is the latest 1.34.x patch — matches the
# `boto3>=1.34` constraint in `app/server/setup.py` while staying on
# the boto3-1.34 minor (avoids the 1.35+ breaking changes around
# AWS SigV4a region resolution that haven't been validated against
# our offsite-backup workflow).
SUMMARY = "Low-level, data-driven core of boto 3."
DESCRIPTION = "A low-level interface to a growing number of Amazon Web Services. The botocore package is the foundation for the AWS CLI as well as boto3."
HOMEPAGE = "https://github.com/boto/botocore"
SECTION = "devel/python"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://LICENSE.txt;md5=2ee41112a44fe7014dce33e26468ba93"

SRC_URI[sha256sum] = "adc23be4fb99ad31961236342b7cbf3c0bfc62532cd02852196032e8c0d682f3"

inherit pypi setuptools3

RDEPENDS:${PN} += " \
    python3-dateutil \
    python3-jmespath \
    python3-json \
    python3-logging \
    python3-urllib3 \
"

BBCLASSEXTEND = "native nativesdk"
