# REQ: SWR-021, SWR-049; RISK: RISK-010, RISK-018; SEC: SC-010, SC-019; TEST: TC-021, TC-044
# =============================================================
# first-boot — Create /data directory structure on first boot
#
# In production images (LUKS_ENABLED = "1"), also installs the
# LUKS encryption service that formats /data with Adiantum
# cipher before the directory setup runs.
# =============================================================
SUMMARY = "First boot setup for Home Monitor OS"
DESCRIPTION = "Creates the /data directory structure on first boot."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://first-boot-setup.sh \
    file://luks-first-boot.sh \
    "

S = "${WORKDIR}"

# Set to "1" in production image recipes to enable LUKS encryption
LUKS_ENABLED ?= "0"

inherit systemd

do_install() {
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/first-boot-setup.sh ${D}/opt/monitor/scripts/first-boot-setup.sh

    install -d ${D}${systemd_system_unitdir}

    # --- Main first-boot service (always installed) ---
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

    # --- LUKS first-boot service (production only) ---
    if [ "${LUKS_ENABLED}" = "1" ]; then
        install -m 0755 ${WORKDIR}/luks-first-boot.sh ${D}/opt/monitor/scripts/luks-first-boot.sh

        cat > ${D}${systemd_system_unitdir}/luks-first-boot.service << 'UNIT'
[Unit]
Description=LUKS encrypt /data partition (ADR-0010)
DefaultDependencies=no
After=local-fs-pre.target systemd-udevd.service
Before=local-fs.target first-boot-setup.service
ConditionPathExists=!/data/.luks-done

[Service]
Type=oneshot
ExecStart=/opt/monitor/scripts/luks-first-boot.sh
RemainAfterExit=yes
TimeoutStartSec=300

[Install]
WantedBy=local-fs.target
UNIT

        # Make first-boot-setup wait for LUKS to finish
        install -d ${D}${systemd_system_unitdir}/first-boot-setup.service.d
        cat > ${D}${systemd_system_unitdir}/first-boot-setup.service.d/luks.conf << 'DROP'
[Unit]
After=luks-first-boot.service
Requires=luks-first-boot.service
DROP
    fi
}

SYSTEMD_SERVICE:${PN} = "first-boot-setup.service"
SYSTEMD_SERVICE:${PN} += "${@'luks-first-boot.service' if d.getVar('LUKS_ENABLED') == '1' else ''}"
SYSTEMD_AUTO_ENABLE = "enable"

FILES:${PN} = " \
    /opt/monitor/scripts/first-boot-setup.sh \
    ${systemd_system_unitdir}/first-boot-setup.service \
    "

# Conditionally include LUKS files
FILES:${PN} += "${@'/opt/monitor/scripts/luks-first-boot.sh \
    ${systemd_system_unitdir}/luks-first-boot.service \
    ${systemd_system_unitdir}/first-boot-setup.service.d/luks.conf' \
    if d.getVar('LUKS_ENABLED') == '1' else ''}"

# LUKS requires cryptsetup on the target
RDEPENDS:${PN} += "${@'cryptsetup' if d.getVar('LUKS_ENABLED') == '1' else ''}"
