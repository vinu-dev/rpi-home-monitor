# REQ: SWR-046, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044
# =============================================================
# monitor-server — Home monitoring web application
# Installs from app/server/ in the repository
# =============================================================
SUMMARY = "Home monitoring server with web UI and video recording"
DESCRIPTION = "Flask-based web server that manages RTSP camera streams, \
records video using ffmpeg, and provides a mobile-friendly web interface."
# Project is AGPL-3.0-only (see repo-root LICENSE). A prior revision of
# this recipe declared MIT — that was incorrect and contaminated SBOM /
# license-report output for the shipped server image (issue #120).
LICENSE = "AGPL-3.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/AGPL-3.0-only;md5=73f1eb20517c55bf9493b7dd6e480788"

# Source files from app/server/ directory in the repo
# Plus app/shared/ for cross-package helpers (release_version)
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../app/server:${THISDIR}/../../../app/shared:"

SRC_URI = " \
    file://monitor/ \
    file://release_version/release_version.py \
    file://ota_policy/ota_policy.py \
    file://status_led/status_led.py \
    file://status_led/home-monitor-ledctl \
    file://status_led/home-monitor-led-init.service \
    file://config/monitor.service \
    file://config/monitor-privileged-helper.service \
    file://config/monitor-avahi-pin.service \
    file://config/monitor-avahi-pin.timer \
    file://config/monitor-hotspot.service \
    file://config/monitor-hotspot.sh \
    file://config/monitor-network-dispatcher.sh \
    file://config/nginx-monitor.conf \
    file://config/nginx-client-cert.d/provisioning-client-ca.conf \
    file://config/generated/trust/home-monitor-provisioning-ca.crt \
    file://config/nftables-server.conf \
    file://config/captive-portal-dnsmasq.conf \
    file://config/avahi-homemonitor.service \
    file://config/avahi-daemon-home-monitor.conf \
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
    python3-cryptography \
    python3-pyotp \
    python3-boto3 \
    python3-zeroconf \
    ffmpeg \
    util-linux \
    e2fsprogs-mke2fs \
    nginx \
    openssl \
    nftables \
    avahi-daemon \
    "

inherit systemd useradd

SYSTEMD_SERVICE:${PN} = "home-monitor-led-init.service monitor-avahi-pin.service monitor-avahi-pin.timer monitor-privileged-helper.service monitor.service monitor-hotspot.service"
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

    # Shared release_version helper (single source of truth in
    # app/shared/release_version/; identical copy installed in the
    # camera-streamer image). See docs/architecture/versioning.md.
    # The file:// fetcher preserves the subdir name under WORKDIR.
    install -m 0644 ${WORKDIR}/release_version/release_version.py \
        ${D}/opt/monitor/monitor/release_version.py

    # Shared OTA policy helper: server-side staging and camera-side
    # receiver code must make the same downgrade/stale-bundle decision.
    install -m 0644 ${WORKDIR}/ota_policy/ota_policy.py \
        ${D}/opt/monitor/monitor/ota_policy.py

    install -m 0644 ${WORKDIR}/status_led/status_led.py \
        ${D}/opt/monitor/monitor/status_led.py

    # Create data directories (will be on /data partition in production)
    install -d ${D}/opt/monitor/data/recordings
    install -d ${D}/opt/monitor/data/live
    install -d ${D}/opt/monitor/data/config
    install -d ${D}/opt/monitor/data/certs
    install -d ${D}/opt/monitor/data/logs

    # Hotspot setup script
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/config/monitor-hotspot.sh ${D}/opt/monitor/scripts/monitor-hotspot.sh
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/status_led/home-monitor-ledctl ${D}${bindir}/home-monitor-ledctl

    # Systemd services
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/config/monitor.service ${D}${systemd_system_unitdir}/monitor.service
    install -m 0644 ${WORKDIR}/config/monitor-privileged-helper.service ${D}${systemd_system_unitdir}/monitor-privileged-helper.service
    install -m 0644 ${WORKDIR}/config/monitor-avahi-pin.service ${D}${systemd_system_unitdir}/monitor-avahi-pin.service
    install -m 0644 ${WORKDIR}/config/monitor-avahi-pin.timer ${D}${systemd_system_unitdir}/monitor-avahi-pin.timer
    install -m 0644 ${WORKDIR}/config/monitor-hotspot.service ${D}${systemd_system_unitdir}/monitor-hotspot.service
    install -m 0644 ${WORKDIR}/status_led/home-monitor-led-init.service ${D}${systemd_system_unitdir}/home-monitor-led-init.service

    # Nginx config
    install -d ${D}${sysconfdir}/nginx/sites-enabled
    install -m 0644 ${WORKDIR}/config/nginx-monitor.conf ${D}${sysconfdir}/nginx/sites-enabled/monitor.conf
    install -d ${D}${sysconfdir}/nginx/client-cert.d
    install -m 0644 ${WORKDIR}/config/nginx-client-cert.d/provisioning-client-ca.conf \
        ${D}${sysconfdir}/nginx/client-cert.d/provisioning-client-ca.conf
    install -d ${D}${sysconfdir}/home-monitor/trust
    install -m 0644 ${WORKDIR}/config/generated/trust/home-monitor-provisioning-ca.crt \
        ${D}${sysconfdir}/home-monitor/trust/home-monitor-provisioning-ca.crt

    # Firewall rules
    install -d ${D}${sysconfdir}/nftables.d
    install -m 0644 ${WORKDIR}/config/nftables-server.conf ${D}${sysconfdir}/nftables.d/monitor.conf

    # Logrotate
    install -d ${D}${sysconfdir}/logrotate.d
    install -m 0644 ${WORKDIR}/config/logrotate-monitor.conf ${D}${sysconfdir}/logrotate.d/monitor

    # Captive portal DNS redirect (NM shared-mode dnsmasq config)
    install -d ${D}${sysconfdir}/NetworkManager/dnsmasq-shared.d
    install -m 0644 ${WORKDIR}/config/captive-portal-dnsmasq.conf ${D}${sysconfdir}/NetworkManager/dnsmasq-shared.d/captive-portal.conf
    install -d ${D}${sysconfdir}/NetworkManager/dispatcher.d
    install -m 0755 ${WORKDIR}/config/monitor-network-dispatcher.sh ${D}${sysconfdir}/NetworkManager/dispatcher.d/20-home-monitor-lan-identity

    # Avahi mDNS service advertisement (cameras find server at homemonitor.local)
    install -d ${D}${sysconfdir}/avahi/services
    install -m 0644 ${WORKDIR}/config/avahi-homemonitor.service ${D}${sysconfdir}/avahi/services/homemonitor.service
    install -d ${D}${systemd_system_unitdir}/avahi-daemon.service.d
    install -m 0644 ${WORKDIR}/config/avahi-daemon-home-monitor.conf ${D}${systemd_system_unitdir}/avahi-daemon.service.d/10-home-monitor.conf
}

FILES:${PN} = " \
    /opt/monitor \
    ${bindir}/home-monitor-ledctl \
    ${systemd_system_unitdir}/home-monitor-led-init.service \
    ${systemd_system_unitdir}/monitor.service \
    ${systemd_system_unitdir}/monitor-privileged-helper.service \
    ${systemd_system_unitdir}/monitor-avahi-pin.service \
    ${systemd_system_unitdir}/monitor-avahi-pin.timer \
    ${systemd_system_unitdir}/monitor-hotspot.service \
    ${systemd_system_unitdir}/avahi-daemon.service.d/10-home-monitor.conf \
    ${sysconfdir}/nginx/sites-enabled/monitor.conf \
    ${sysconfdir}/nginx/client-cert.d/provisioning-client-ca.conf \
    ${sysconfdir}/home-monitor/trust/home-monitor-provisioning-ca.crt \
    ${sysconfdir}/nftables.d/monitor.conf \
    ${sysconfdir}/logrotate.d/monitor \
    ${sysconfdir}/NetworkManager/dnsmasq-shared.d/captive-portal.conf \
    ${sysconfdir}/NetworkManager/dispatcher.d/20-home-monitor-lan-identity \
    ${sysconfdir}/avahi/services/homemonitor.service \
    "
