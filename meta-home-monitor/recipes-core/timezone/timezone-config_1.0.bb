SUMMARY = "Default timezone configuration"
DESCRIPTION = "Sets default timezone to UTC. Change at runtime: timedatectl set-timezone <zone>"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

RDEPENDS:${PN} = "tzdata"

inherit allarch

do_install() {
    install -d ${D}${sysconfdir}
    echo "UTC" > ${D}${sysconfdir}/timezone
    install -d ${D}${datadir}/zoneinfo
    ln -sf ../usr/share/zoneinfo/UTC ${D}${sysconfdir}/localtime
}

FILES:${PN} = "${sysconfdir}/timezone ${sysconfdir}/localtime"

# Don't conflict with base-files
RCONFLICTS:${PN} = ""
ALLOW_EMPTY:${PN} = "1"
