# NTP server configuration for Home Monitor OS
# Use drop-in config to avoid conflicting with systemd's own timesyncd.conf
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI += "file://timesyncd.conf"

do_install:append() {
    # Install as drop-in override (does not conflict with systemd package)
    install -d ${D}${sysconfdir}/systemd/timesyncd.conf.d
    install -m0644 ${WORKDIR}/timesyncd.conf ${D}${sysconfdir}/systemd/timesyncd.conf.d/00-home-monitor.conf
}

FILES:${PN} += "${sysconfdir}/systemd/timesyncd.conf.d/00-home-monitor.conf"
