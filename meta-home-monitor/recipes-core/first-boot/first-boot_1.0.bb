# =============================================================
# first-boot — Create /data directory structure on first boot
# =============================================================
SUMMARY = "First boot setup for Home Monitor OS"
DESCRIPTION = "Creates the /data directory structure on first boot."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://first-boot-setup.sh"

S = "${WORKDIR}"

inherit systemd

do_install() {
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/first-boot-setup.sh ${D}/opt/monitor/scripts/first-boot-setup.sh

    install -d ${D}${systemd_system_unitdir}
    cat > ${D}${systemd_system_unitdir}/first-boot-setup.service << 'UNIT'
[Unit]
Description=First boot data directory setup
After=local-fs.target
Requires=local-fs.target
Before=monitor.service camera-streamer.service monitor-certs.service monitor-hotspot.service
ConditionPathExists=!/data/.first-boot-done

[Service]
Type=oneshot
ExecStart=/opt/monitor/scripts/first-boot-setup.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
}

SYSTEMD_SERVICE:${PN} = "first-boot-setup.service"
SYSTEMD_AUTO_ENABLE = "enable"

FILES:${PN} = " \
    /opt/monitor/scripts/first-boot-setup.sh \
    ${systemd_system_unitdir}/first-boot-setup.service \
    "
