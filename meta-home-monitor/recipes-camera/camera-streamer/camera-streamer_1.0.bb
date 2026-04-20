# =============================================================
# camera-streamer — RTSP streaming service for PiHut ZeroCam
# Installs from app/camera/ in the repository
# =============================================================
SUMMARY = "Camera RTSP streamer for home monitoring"
DESCRIPTION = "Captures video from the PiHut ZeroCam via v4l2 \
and streams it over RTSPS to the home monitoring server."
# Project is AGPL-3.0-only (see repo-root LICENSE). A prior revision of
# this recipe declared MIT — that was incorrect and contaminated SBOM /
# license-report output for the shipped camera image (issue #120).
LICENSE = "AGPL-3.0-only"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/AGPL-3.0-only;md5=eb1e647870add0502f8f010b19de32af"

# Source files from app/camera/ directory in the repo
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../app/camera:"

SRC_URI = " \
    file://camera_streamer/ \
    file://config/camera-streamer.service \
    file://config/camera-hotspot.service \
    file://config/camera-hotspot.sh \
    file://config/nftables-camera.conf \
    file://config/captive-portal-dnsmasq.conf \
    file://config/camera.conf.default \
    file://config/ensure-camera-overlay.sh \
    file://config/ensure-camera-overlay.service \
    file://config/timesyncd-camera.conf \
    file://config/camera-ota-installer.service \
    file://config/camera-ota-installer.path \
    file://config/camera-ota-tmpfiles.conf \
    file://scripts/camera-ota-installer.sh \
    file://setup.py \
    "

S = "${WORKDIR}"

RDEPENDS:${PN} = " \
    python3 \
    ffmpeg \
    v4l-utils \
    avahi-daemon \
    avahi-utils \
    openssl \
    nftables \
    swupdate \
    u-boot-fw-utils \
    "

inherit systemd useradd

SYSTEMD_SERVICE:${PN} = "camera-streamer.service camera-hotspot.service ensure-camera-overlay.service camera-ota-installer.path"
SYSTEMD_AUTO_ENABLE = "enable"

# Create camera system user/group
USERADD_PACKAGES = "${PN}"
USERADD_PARAM:${PN} = "-r -d /opt/camera -s /bin/false -g camera -G video camera"
GROUPADD_PARAM:${PN} = "-r camera"

do_install() {
    # Install the Python application
    install -d ${D}/opt/camera
    cp -r ${WORKDIR}/camera_streamer ${D}/opt/camera/
    install -m 0644 ${WORKDIR}/setup.py ${D}/opt/camera/

    # Default config (copied to /data on first boot)
    install -m 0644 ${WORKDIR}/config/camera.conf.default ${D}/opt/camera/camera.conf.default

    # Scripts (WiFi provisioning, factory reset, boot-time overlay check)
    install -d ${D}/opt/camera/scripts
    install -m 0755 ${WORKDIR}/config/camera-hotspot.sh ${D}/opt/camera/scripts/camera-hotspot.sh
    install -m 0755 ${WORKDIR}/config/ensure-camera-overlay.sh ${D}/opt/camera/scripts/ensure-camera-overlay.sh

    # Privileged OTA installer (runs as root via path-activated service)
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/scripts/camera-ota-installer.sh ${D}${bindir}/camera-ota-installer

    # Systemd services
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/config/camera-streamer.service ${D}${systemd_system_unitdir}/camera-streamer.service
    install -m 0644 ${WORKDIR}/config/camera-hotspot.service ${D}${systemd_system_unitdir}/camera-hotspot.service
    install -m 0644 ${WORKDIR}/config/ensure-camera-overlay.service ${D}${systemd_system_unitdir}/ensure-camera-overlay.service
    install -m 0644 ${WORKDIR}/config/camera-ota-installer.service ${D}${systemd_system_unitdir}/camera-ota-installer.service
    install -m 0644 ${WORKDIR}/config/camera-ota-installer.path ${D}${systemd_system_unitdir}/camera-ota-installer.path

    # tmpfiles.d rule that creates the /var/lib/camera-ota spool
    install -d ${D}${sysconfdir}/tmpfiles.d
    install -m 0644 ${WORKDIR}/config/camera-ota-tmpfiles.conf ${D}${sysconfdir}/tmpfiles.d/camera-ota.conf

    # Firewall rules
    install -d ${D}${sysconfdir}/nftables.d
    install -m 0644 ${WORKDIR}/config/nftables-camera.conf ${D}${sysconfdir}/nftables.d/camera.conf

    # Captive portal DNS redirect (NM shared-mode dnsmasq config)
    install -d ${D}${sysconfdir}/NetworkManager/dnsmasq-shared.d
    install -m 0644 ${WORKDIR}/config/captive-portal-dnsmasq.conf ${D}${sysconfdir}/NetworkManager/dnsmasq-shared.d/captive-portal.conf

    # Camera NTP drop-in — prefer LAN server as time source (ADR-0019)
    install -d ${D}${sysconfdir}/systemd/timesyncd.conf.d
    install -m 0644 ${WORKDIR}/config/timesyncd-camera.conf ${D}${sysconfdir}/systemd/timesyncd.conf.d/10-home-camera.conf
}

FILES:${PN} = " \
    /opt/camera \
    ${bindir}/camera-ota-installer \
    ${systemd_system_unitdir}/camera-streamer.service \
    ${systemd_system_unitdir}/camera-hotspot.service \
    ${systemd_system_unitdir}/ensure-camera-overlay.service \
    ${systemd_system_unitdir}/camera-ota-installer.service \
    ${systemd_system_unitdir}/camera-ota-installer.path \
    ${sysconfdir}/nftables.d/camera.conf \
    ${sysconfdir}/NetworkManager/dnsmasq-shared.d/captive-portal.conf \
    ${sysconfdir}/systemd/timesyncd.conf.d/10-home-camera.conf \
    ${sysconfdir}/tmpfiles.d/camera-ota.conf \
    "
