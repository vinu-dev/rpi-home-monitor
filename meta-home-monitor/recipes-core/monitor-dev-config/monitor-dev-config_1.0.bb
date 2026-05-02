# REQ: SWR-021, SWR-049; RISK: RISK-010, RISK-018; SEC: SC-010, SC-019; TEST: TC-021, TC-044
# =============================================================
# monitor-dev-config — Dev-only configuration for Home Monitor
#
# Installed ONLY in -dev image variants. Prod images don't
# include this recipe.
#
# What it does:
#   Debug logging — LOG_LEVEL=DEBUG for app services
#
# First boot on dev images still goes through the setup wizard
# (captive portal / HomeCam-Setup hotspot) to collect WiFi
# credentials and admin password. There are no pre-set credentials
# — the user enters them during setup, same as on prod. See ADR-0007.
# =============================================================
SUMMARY = "Development configuration for Home Monitor"
DESCRIPTION = "Debug logging for dev builds. See ADR-0007."
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

S = "${WORKDIR}"

do_install() {
    # Monitor server debug logging
    install -d ${D}${sysconfdir}/systemd/system/monitor.service.d
    cat > ${D}${sysconfdir}/systemd/system/monitor.service.d/10-dev-logging.conf << 'CONF'
[Service]
Environment=LOG_LEVEL=DEBUG
CONF

    # Camera streamer debug logging
    install -d ${D}${sysconfdir}/systemd/system/camera-streamer.service.d
    cat > ${D}${sysconfdir}/systemd/system/camera-streamer.service.d/10-dev-logging.conf << 'CONF'
[Service]
Environment=LOG_LEVEL=DEBUG
CONF
}

FILES:${PN} = " \
    ${sysconfdir}/systemd/system/monitor.service.d/10-dev-logging.conf \
    ${sysconfdir}/systemd/system/camera-streamer.service.d/10-dev-logging.conf \
    "
