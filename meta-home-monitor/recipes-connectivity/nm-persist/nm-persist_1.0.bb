# REQ: SWR-046, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044
# =============================================================
# nm-persist — Persist NetworkManager connections across OTA
#
# WiFi credentials live in /etc/NetworkManager/system-connections/
# which is on rootfs and gets wiped by A/B OTA updates. This
# recipe bind-mounts /data/network/system-connections/ over the
# rootfs directory so credentials survive rootfs replacements.
#
# On first boot (or after OTA wipe), seeds from rootfs defaults.
# =============================================================
SUMMARY = "Persist NetworkManager connections on /data across OTA updates"
DESCRIPTION = "Bind-mounts /data/network/system-connections over rootfs NM connections directory"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://nm-persist.sh \
    file://10-home-monitor-hostname.conf \
    "

S = "${WORKDIR}"

inherit systemd

do_install() {
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/nm-persist.sh ${D}/opt/monitor/scripts/nm-persist.sh

    install -d ${D}${sysconfdir}/NetworkManager/conf.d
    install -m 0644 ${WORKDIR}/10-home-monitor-hostname.conf \
        ${D}${sysconfdir}/NetworkManager/conf.d/10-home-monitor-hostname.conf

    install -d ${D}${systemd_system_unitdir}

    # Service runs every boot, before NetworkManager
    cat > ${D}${systemd_system_unitdir}/nm-persist.service << 'UNIT'
[Unit]
Description=Persist NetworkManager connections on /data
DefaultDependencies=no
After=local-fs.target data.mount
Before=NetworkManager.service
Wants=local-fs.target

[Service]
Type=oneshot
ExecStart=/opt/monitor/scripts/nm-persist.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
}

SYSTEMD_SERVICE:${PN} = "nm-persist.service"
SYSTEMD_AUTO_ENABLE = "enable"

FILES:${PN} = " \
    /opt/monitor/scripts/nm-persist.sh \
    ${sysconfdir}/NetworkManager/conf.d/10-home-monitor-hostname.conf \
    ${systemd_system_unitdir}/nm-persist.service \
    "

RDEPENDS:${PN} = "util-linux-mount"
