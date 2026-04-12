# Home Monitor SWUpdate configuration (ADR-0008)
# - Custom defconfig with U-Boot A/B handler and Ed25519 verification
# - Post-boot health check service for automatic rollback
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

inherit systemd

SRC_URI += " \
    file://swupdate-check.sh \
    "

do_install:append() {
    # Install health check script
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/swupdate-check.sh ${D}/opt/monitor/scripts/swupdate-check.sh

    # Install health check systemd service
    install -d ${D}${systemd_system_unitdir}
    cat > ${D}${systemd_system_unitdir}/swupdate-check.service << 'UNIT'
[Unit]
Description=Post-boot OTA health check (ADR-0008)
After=network-online.target monitor.service camera-streamer.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/monitor/scripts/swupdate-check.sh
RemainAfterExit=yes
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
UNIT
}

SYSTEMD_SERVICE:${PN} += "swupdate-check.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

FILES:${PN} += " \
    /opt/monitor/scripts/swupdate-check.sh \
    ${systemd_system_unitdir}/swupdate-check.service \
    "
