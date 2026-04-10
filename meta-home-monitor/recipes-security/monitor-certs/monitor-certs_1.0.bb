# =============================================================
# monitor-certs — First-boot CA and TLS certificate generation
# =============================================================
SUMMARY = "Certificate generator for Home Monitor TLS"
DESCRIPTION = "Generates a local Certificate Authority and server \
TLS certificate on first boot for HTTPS and mTLS camera auth."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = "file://generate-certs.sh"

S = "${WORKDIR}"

RDEPENDS:${PN} = "openssl"

inherit systemd

do_install() {
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/generate-certs.sh ${D}/opt/monitor/scripts/generate-certs.sh

    # Systemd oneshot service to run on first boot
    install -d ${D}${systemd_system_unitdir}
    cat > ${D}${systemd_system_unitdir}/monitor-certs.service << 'UNIT'
[Unit]
Description=Generate TLS certificates on first boot
After=local-fs.target first-boot-setup.service
Requires=local-fs.target
Before=nginx.service monitor.service
ConditionPathExists=!/data/certs/ca.crt

[Service]
Type=oneshot
ExecStart=/opt/monitor/scripts/generate-certs.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
}

SYSTEMD_SERVICE:${PN} = "monitor-certs.service"
SYSTEMD_AUTO_ENABLE = "enable"

FILES:${PN} = " \
    /opt/monitor/scripts/generate-certs.sh \
    ${systemd_system_unitdir}/monitor-certs.service \
    "
