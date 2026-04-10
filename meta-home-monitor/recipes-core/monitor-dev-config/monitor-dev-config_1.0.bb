# =============================================================
# monitor-dev-config — Debug logging for dev builds only
#
# Installs systemd drop-in overrides that set LOG_LEVEL=DEBUG
# for both monitor.service and camera-streamer.service.
#
# Only included in -dev image variants. Prod images don't
# include this, so apps default to LOG_LEVEL=WARNING.
# =============================================================
SUMMARY = "Development logging configuration for Home Monitor"
DESCRIPTION = "Systemd drop-ins that enable DEBUG logging for dev builds."
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
