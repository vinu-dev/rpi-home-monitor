# REQ: SWR-046, SWR-072; RISK: RISK-019; SEC: SC-018; TEST: TC-043, TC-045
# Yocto recipe for s3transfer — boto3's high-level S3 transfer manager.
#
# Vendored alongside python3-boto3 / python3-botocore — boto3
# RDEPENDS on s3transfer for `boto3.client('s3').upload_file()` and
# friends, which the offsite-backup worker uses to push recordings
# (#243). Not provided by meta-openembedded.
SUMMARY = "An Amazon S3 Transfer Manager for Python"
DESCRIPTION = "S3transfer is a Python library for managing Amazon S3 transfers. This project is maintained and published by Amazon Web Services."
HOMEPAGE = "https://github.com/boto/s3transfer"
SECTION = "devel/python"
LICENSE = "Apache-2.0"
LIC_FILES_CHKSUM = "file://LICENSE.txt;md5=3b83ef96387f14655fc854ddc3c6bd57"

SRC_URI[sha256sum] = "0711534e9356d3cc692fdde846b4a1e4b0cb6519971860796e6bc4c7aea00ef6"

inherit pypi setuptools3

RDEPENDS:${PN} += " \
    python3-botocore \
    python3-logging \
    python3-threading \
"

BBCLASSEXTEND = "native nativesdk"
