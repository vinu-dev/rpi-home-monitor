# NTP server configuration for Home Monitor OS
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI += "file://timesyncd.conf"

do_install:append() {
    install -D -m0644 ${WORKDIR}/timesyncd.conf ${D}${sysconfdir}/systemd/timesyncd.conf
}

FILES:${PN} += "${sysconfdir}/systemd/timesyncd.conf"
