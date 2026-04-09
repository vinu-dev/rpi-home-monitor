# =============================================================
# camera-streamer — RTSP streaming service for PiHut ZeroCam
# Installs from app/camera/ in the repository
# =============================================================
SUMMARY = "Camera RTSP streamer for home monitoring"
DESCRIPTION = "Captures video from the PiHut ZeroCam via v4l2 \
and streams it over RTSPS to the home monitoring server."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Source files from app/camera/ directory in the repo
FILESEXTRAPATHS:prepend := "${THISDIR}/../../../app/camera:"

SRC_URI = " \
    file://camera_streamer/ \
    file://config/camera-streamer.service \
    file://config/nftables-camera.conf \
    file://config/camera.conf.default \
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
    "

inherit systemd useradd

SYSTEMD_SERVICE:${PN} = "camera-streamer.service"
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

    # Systemd service
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/config/camera-streamer.service ${D}${systemd_system_unitdir}/camera-streamer.service

    # Firewall rules
    install -d ${D}${sysconfdir}/nftables.d
    install -m 0644 ${WORKDIR}/config/nftables-camera.conf ${D}${sysconfdir}/nftables.d/camera.conf
}

FILES:${PN} = " \
    /opt/camera \
    ${systemd_system_unitdir}/camera-streamer.service \
    ${sysconfdir}/nftables.d/camera.conf \
    "
