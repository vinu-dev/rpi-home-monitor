# REQ: SWR-050; RISK: RISK-018; SEC: SC-019; TEST: TC-044, TC-047
# NTP server configuration for Home Monitor OS
# Use drop-in config to avoid conflicting with systemd's own timesyncd.conf
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI += "file://timesyncd.conf"

do_install:append() {
    # Install as drop-in override (does not conflict with systemd package)
    install -d ${D}${sysconfdir}/systemd/timesyncd.conf.d
    install -m0644 ${WORKDIR}/timesyncd.conf ${D}${sysconfdir}/systemd/timesyncd.conf.d/00-home-monitor.conf

    # Home Monitor uses NetworkManager as the appliance network manager.
    # systemd-networkd-wait-online.service can be pulled in indirectly by
    # generic network-online.target dependencies and then times out on
    # no-carrier interfaces, leaving OTA boots degraded. Mask the unused
    # wait unit at image build time; services that need networking should
    # depend on network.target and tolerate link changes.
    install -d ${D}${sysconfdir}/systemd/system
    ln -sf /dev/null ${D}${sysconfdir}/systemd/system/systemd-networkd-wait-online.service
}

FILES:${PN} += " \
    ${sysconfdir}/systemd/timesyncd.conf.d/00-home-monitor.conf \
    ${sysconfdir}/systemd/system/systemd-networkd-wait-online.service \
"
