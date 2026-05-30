# REQ: SWR-018, SWR-050; RISK: RISK-006, RISK-018; SEC: SC-006, SC-019; TEST: TC-015, TC-044
SUMMARY = "Boot-time GPIO provisioning and factory reset trigger"
DESCRIPTION = "Shared GPIO jumper detector used by server and camera images."
LICENSE = "AGPL-3.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/AGPL-3.0-only;md5=73f1eb20517c55bf9493b7dd6e480788"

FILESEXTRAPATHS:prepend := "${THISDIR}/../../../app/server:"

SRC_URI = " \
    file://config/gpio-trigger.sh \
    file://config/gpio-trigger.service \
    "

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "gpio-trigger.service"
SYSTEMD_AUTO_ENABLE = "enable"

do_install() {
    install -d ${D}/opt/scripts
    install -m 0755 ${WORKDIR}/config/gpio-trigger.sh ${D}/opt/scripts/gpio-trigger.sh

    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/config/gpio-trigger.service ${D}${systemd_system_unitdir}/gpio-trigger.service
}

FILES:${PN} = " \
    /opt/scripts/gpio-trigger.sh \
    ${systemd_system_unitdir}/gpio-trigger.service \
    "
