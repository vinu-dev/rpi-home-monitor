# =============================================================
# monitor-server — Home monitoring web application
# Installs from app/server/ in the repository
# =============================================================
SUMMARY = "Home monitoring server with web UI and video recording"
DESCRIPTION = "Flask-based web server that manages RTSP camera streams, \
records video using ffmpeg, and provides a mobile-friendly web interface."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Source files from app/server/ directory in the repo
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../app/server:"

SRC_URI = " \
    file://monitor/ \
    file://config/monitor.service \
    file://config/monitor-hotspot.service \
    file://config/monitor-hotspot.sh \
    file://config/nginx-monitor.conf \
    file://config/nftables-server.conf \
    file://config/logrotate-monitor.conf \
    file://setup.py \
    file://requirements.txt \
    "

S = "${WORKDIR}"

RDEPENDS:${PN} = " \
    python3 \
    python3-flask \
    python3-jinja2 \
    python3-bcrypt \
    ffmpeg \
    nginx \
    openssl \
    nftables \
    avahi-daemon \
    "

inherit systemd useradd

SYSTEMD_SERVICE:${PN} = "monitor.service monitor-hotspot.service"
SYSTEMD_AUTO_ENABLE = "enable"

# Create monitor system user/group
USERADD_PACKAGES = "${PN}"
USERADD_PARAM:${PN} = "-r -d /opt/monitor -s /bin/false -g monitor -G video monitor"
GROUPADD_PARAM:${PN} = "-r monitor"

do_install() {
    # Install the Python application
    install -d ${D}/opt/monitor
    cp -r ${WORKDIR}/monitor ${D}/opt/monitor/
    install -m 0644 ${WORKDIR}/setup.py ${D}/opt/monitor/
    install -m 0644 ${WORKDIR}/requirements.txt ${D}/opt/monitor/

    # Create data directories (will be on /data partition in production)
    install -d ${D}/opt/monitor/data/recordings
    install -d ${D}/opt/monitor/data/live
    install -d ${D}/opt/monitor/data/config
    install -d ${D}/opt/monitor/data/certs
    install -d ${D}/opt/monitor/data/logs

    # Hotspot setup script
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/config/monitor-hotspot.sh ${D}/opt/monitor/scripts/monitor-hotspot.sh

    # Systemd services
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/config/monitor.service ${D}${systemd_system_unitdir}/monitor.service
    install -m 0644 ${WORKDIR}/config/monitor-hotspot.service ${D}${systemd_system_unitdir}/monitor-hotspot.service

    # Nginx config
    install -d ${D}${sysconfdir}/nginx/sites-enabled
    install -m 0644 ${WORKDIR}/config/nginx-monitor.conf ${D}${sysconfdir}/nginx/sites-enabled/monitor.conf

    # Firewall rules
    install -d ${D}${sysconfdir}/nftables.d
    install -m 0644 ${WORKDIR}/config/nftables-server.conf ${D}${sysconfdir}/nftables.d/monitor.conf

    # Logrotate
    install -d ${D}${sysconfdir}/logrotate.d
    install -m 0644 ${WORKDIR}/config/logrotate-monitor.conf ${D}${sysconfdir}/logrotate.d/monitor
}

FILES:${PN} = " \
    /opt/monitor \
    ${systemd_system_unitdir}/monitor.service \
    ${systemd_system_unitdir}/monitor-hotspot.service \
    ${sysconfdir}/nginx/sites-enabled/monitor.conf \
    ${sysconfdir}/nftables.d/monitor.conf \
    ${sysconfdir}/logrotate.d/monitor \
    "
