# =============================================================
# mediamtx — Lightweight RTSP/RTMP/HLS/WebRTC server
# Receives camera RTSP pushes and republishes for server consumption
# =============================================================
SUMMARY = "MediaMTX RTSP media server"
DESCRIPTION = "Lightweight ready-to-use RTSP/RTMP/HLS/WebRTC server \
that receives camera RTSP pushes for the home monitor system."
HOMEPAGE = "https://github.com/bluenviron/mediamtx"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

# Pre-built binary for aarch64
SRC_URI = " \
    https://github.com/bluenviron/mediamtx/releases/download/v${PV}/mediamtx_v${PV}_linux_arm64v8.tar.gz;name=binary \
    file://mediamtx.yml \
    file://mediamtx.service \
    "
SRC_URI[binary.sha256sum] = "FIXME_UPDATE_WITH_ACTUAL_CHECKSUM"

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "mediamtx.service"
SYSTEMD_AUTO_ENABLE = "enable"

# Pre-built binary — skip compilation
do_compile[noexec] = "1"

do_install() {
    # Install binary
    install -d ${D}${bindir}
    install -m 0755 ${WORKDIR}/mediamtx ${D}${bindir}/mediamtx

    # Install config
    install -d ${D}${sysconfdir}/mediamtx
    install -m 0644 ${WORKDIR}/mediamtx.yml ${D}${sysconfdir}/mediamtx/mediamtx.yml

    # Install systemd service
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/mediamtx.service ${D}${systemd_system_unitdir}/mediamtx.service
}

FILES:${PN} = " \
    ${bindir}/mediamtx \
    ${sysconfdir}/mediamtx \
    ${systemd_system_unitdir}/mediamtx.service \
    "

# Pre-built Go binary — insane-skip needed for stripped binary
INSANE_SKIP:${PN} = "already-stripped ldflags"
