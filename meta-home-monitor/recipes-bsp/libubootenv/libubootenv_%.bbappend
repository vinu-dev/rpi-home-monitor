# REQ: SWR-046, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

# Provide fw_env.config so fw_printenv/fw_setenv can find the U-Boot
# environment stored on the FAT boot partition (/boot/uboot.env).
SRC_URI += "file://fw_env.config"

do_install:append() {
    install -d ${D}${sysconfdir}
    install -m 0644 ${WORKDIR}/fw_env.config ${D}${sysconfdir}/fw_env.config
}

FILES:${PN} += "${sysconfdir}/fw_env.config"
