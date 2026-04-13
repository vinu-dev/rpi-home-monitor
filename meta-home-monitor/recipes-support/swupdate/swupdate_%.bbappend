# Home Monitor SWUpdate configuration (ADR-0008)
# - Custom defconfig with U-Boot A/B handler and CMS/Ed25519 verification
# - conf.d override supplies -k flag so swupdate daemon finds the public cert
# - Post-boot health check service for automatic rollback
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

inherit systemd

SRC_URI += " \
    file://swupdate-check.sh \
    file://swupdate.cfg \
    file://swupdate-args \
    file://swupdate-public.crt \
    "

do_install:append() {
    # --- Health check script + service ---
    install -d ${D}/opt/monitor/scripts
    install -m 0755 ${WORKDIR}/swupdate-check.sh ${D}/opt/monitor/scripts/swupdate-check.sh

    install -d ${D}${systemd_system_unitdir}
    cat > ${D}${systemd_system_unitdir}/swupdate-check.service << 'UNIT'
[Unit]
Description=Post-boot OTA health check (ADR-0008)
After=network-online.target monitor.service camera-streamer.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/monitor/scripts/swupdate-check.sh
RemainAfterExit=yes
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
UNIT

    # --- swupdate config ---
    install -d ${D}${sysconfdir}/swupdate
    install -m 0644 ${WORKDIR}/swupdate.cfg ${D}${sysconfdir}/swupdate.cfg

    # --- conf.d override: pass -k so daemon finds the signing cert ---
    install -d ${D}${sysconfdir}/swupdate/conf.d
    install -m 0644 ${WORKDIR}/swupdate-args ${D}${sysconfdir}/swupdate/conf.d/00-home-monitor

    # --- OTA signing public certificate (verified at bundle install time) ---
    install -m 0644 ${WORKDIR}/swupdate-public.crt ${D}${sysconfdir}/swupdate-public.crt

    # --- hwrevision: identifies hardware for OTA compatibility checking ---
    # Must match hardware-compatibility in sw-description templates.
    if [ "${MACHINE}" = "raspberrypi0-2w-64" ]; then
        printf "rpi-zero2w-camera 1.0\n" > ${D}${sysconfdir}/hwrevision
    else
        printf "rpi-4b-server 1.0\n" > ${D}${sysconfdir}/hwrevision
    fi

    # --- sw-versions: tracks installed software version ---
    # Updated by swupdate post-install scripts after an OTA update.
    printf "home-monitor-image 1.1.0\n" > ${D}${sysconfdir}/sw-versions
}

SYSTEMD_SERVICE:${PN} += "swupdate-check.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

FILES:${PN} += " \
    /opt/monitor/scripts/swupdate-check.sh \
    ${systemd_system_unitdir}/swupdate-check.service \
    ${sysconfdir}/swupdate.cfg \
    ${sysconfdir}/swupdate/conf.d/00-home-monitor \
    ${sysconfdir}/swupdate-public.crt \
    ${sysconfdir}/hwrevision \
    ${sysconfdir}/sw-versions \
    "
