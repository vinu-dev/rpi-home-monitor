# REQ: SWR-006, SWR-031, SWR-050; RISK: RISK-001, RISK-017; SEC: SC-016, SC-019; TEST: TC-001, TC-028, TC-044
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
    file://mediamtx-on-demand.sh \
    "
SRC_URI[binary.sha256sum] = "6ae3e3d78a770ed28ae26f8e8b474387e9d44ee88d419a245e48530062bdb629"

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

    # Install on-demand hook script (ADR-0017)
    install -d ${D}/opt/monitor/bin
    install -m 0755 ${WORKDIR}/mediamtx-on-demand.sh ${D}/opt/monitor/bin/mediamtx-on-demand.sh
}

FILES:${PN} = " \
    ${bindir}/mediamtx \
    ${sysconfdir}/mediamtx \
    ${systemd_system_unitdir}/mediamtx.service \
    /opt/monitor/bin/mediamtx-on-demand.sh \
    "

# Pre-built Go binary — insane-skip needed for stripped binary
INSANE_SKIP:${PN} = "already-stripped ldflags"
