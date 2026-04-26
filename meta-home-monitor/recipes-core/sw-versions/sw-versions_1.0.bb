SUMMARY = "Software versions file for SWUpdate"
DESCRIPTION = "Provides /etc/sw-versions for SWUpdate version tracking"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# NOTE: no SRC_URI — the file is generated at build time from
# ${DISTRO_VERSION} (which reads the repo-root VERSION file via
# the distro conf). The static baseline that used to live in
# files/sw-versions hard-coded "home-monitor 1.0.0" and shipped
# wrong on every fresh-flashed prod card. See
# docs/architecture/versioning.md §C and the v1.4.3 CHANGELOG.

inherit allarch

do_install() {
    install -d ${D}${sysconfdir}
    # Single line, "<component> <version>" — matches what SWUpdate's
    # post-install hook would write after a successful OTA. Keeping
    # the SAME format means the SWUpdate identify mechanism still
    # works without churn; what changes is that fresh-flashed images
    # no longer ship with a stale "1.0.0" placeholder.
    echo "home-monitor ${DISTRO_VERSION}" > ${D}${sysconfdir}/sw-versions
    chmod 0644 ${D}${sysconfdir}/sw-versions
}

FILES:${PN} = "${sysconfdir}/sw-versions"
