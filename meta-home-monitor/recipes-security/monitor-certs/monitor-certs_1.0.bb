# REQ: SWR-034, SWR-043; RISK: RISK-002, RISK-019; SEC: SC-017; TEST: TC-032, TC-040
# =============================================================
# monitor-certs — First-boot CA and TLS certificate generation
# =============================================================
SUMMARY = "Certificate generator for Home Monitor TLS"
DESCRIPTION = "Generates a local Certificate Authority and server \
TLS certificate on first boot for HTTPS and mTLS camera auth."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://generate-certs.sh \
    file://monitor-certs.service \
    "

S = "${WORKDIR}"

RDEPENDS:${PN} = "openssl"

inherit systemd

do_install() {
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/generate-certs.sh ${D}/opt/monitor/scripts/generate-certs.sh

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/monitor-certs.service ${D}${systemd_system_unitdir}/monitor-certs.service
}

SYSTEMD_SERVICE:${PN} = "monitor-certs.service"
SYSTEMD_AUTO_ENABLE = "enable"

FILES:${PN} = " \
    /opt/monitor/scripts/generate-certs.sh \
    ${systemd_system_unitdir}/monitor-certs.service \
    "
