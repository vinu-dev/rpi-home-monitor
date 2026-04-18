# Home Monitor SWUpdate configuration (ADR-0008, ADR-0014)
# - Custom defconfig with U-Boot A/B handler and optional CMS certificate verification
# - conf.d override supplies -k flag so swupdate daemon finds the public cert
# - Post-boot health check service for automatic rollback
#
# SWUPDATE_SIGNING controls whether image signature verification is compiled in:
#   "0" (default) — dev builds: CONFIG_SIGNED_IMAGES disabled, no cert needed
#   "1"           — prod builds: CONFIG_SIGNED_IMAGES enabled, cert baked into image
#
# See ADR-0014 for rationale.
FILESEXTRAPATHS:prepend := "${THISDIR}/files/generated:${THISDIR}/files:"

inherit systemd

# SWUPDATE_SIGNING: set to "1" in local.conf to enable for production builds.
# Default is "0" (dev) — see ADR-0014.
SWUPDATE_SIGNING ??= "0"

SRC_URI += " \
    file://swupdate-check.sh \
    file://swupdate.cfg \
    file://swupdate-args \
    file://monitor-standby-symlink.sh \
    "

# Include signing cert only when signing is enabled (ADR-0014)
SRC_URI:append = " ${@'file://swupdate-public.crt' if d.getVar('SWUPDATE_SIGNING') == '1' else ''}"

# Patch defconfig before configure: strip signing when SWUPDATE_SIGNING=0 (ADR-0014)
do_configure:prepend() {
    if [ "${SWUPDATE_SIGNING}" != "1" ]; then
        bbnote "SWUPDATE_SIGNING=0: disabling CONFIG_SIGNED_IMAGES in swupdate defconfig (ADR-0014)"
        sed -i 's/^CONFIG_SIGNED_IMAGES=y$/# CONFIG_SIGNED_IMAGES is not set/' ${WORKDIR}/defconfig
        sed -i 's/^CONFIG_SIGALG_CMS=y$/# CONFIG_SIGALG_CMS is not set/' ${WORKDIR}/defconfig
    fi
}

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

    # --- Boot-time /dev/monitor_standby symlink ---
    # Required because swupdate's check_free_space stats the install
    # target before preinst can run — if the symlink is missing at
    # that instant the install is rejected with a bogus "not enough
    # free space" error (measured against /tmp's tmpfs).
    install -m 0755 ${WORKDIR}/monitor-standby-symlink.sh \
        ${D}/opt/monitor/scripts/monitor-standby-symlink.sh

    cat > ${D}${systemd_system_unitdir}/monitor-standby-symlink.service << 'UNIT'
[Unit]
Description=Create /dev/monitor_standby symlink for SWUpdate
# Must be up before any OTA install can be invoked.
DefaultDependencies=no
After=systemd-remount-fs.service local-fs.target
Before=sysinit.target

[Service]
Type=oneshot
ExecStart=/opt/monitor/scripts/monitor-standby-symlink.sh
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
UNIT

    # --- swupdate config ---
    install -d ${D}${sysconfdir}/swupdate
    install -m 0644 ${WORKDIR}/swupdate.cfg ${D}${sysconfdir}/swupdate.cfg

    # --- conf.d: SWUPDATE_ARGS and optional signing cert (ADR-0014) ---
    install -d ${D}${sysconfdir}/swupdate/conf.d
    if [ "${SWUPDATE_SIGNING}" = "1" ]; then
        # Prod: pass -k so the daemon can verify bundle signatures
        install -m 0644 ${WORKDIR}/swupdate-args ${D}${sysconfdir}/swupdate/conf.d/00-home-monitor
        install -m 0644 ${WORKDIR}/swupdate-public.crt ${D}${sysconfdir}/swupdate-public.crt
    else
        # Dev: no signing — just set verbosity, no -k needed
        printf '# Home Monitor OTA args (dev — signing disabled, see ADR-0014)\n' \
            > ${D}${sysconfdir}/swupdate/conf.d/00-home-monitor
        printf 'SWUPDATE_ARGS="-v ${SWUPDATE_EXTRA_ARGS}"\n' \
            >> ${D}${sysconfdir}/swupdate/conf.d/00-home-monitor
    fi

}
# Note: /etc/hwrevision is provided by the hwrevision recipe.
#       /etc/sw-versions is provided by the sw-versions recipe.

SYSTEMD_SERVICE:${PN} += "swupdate-check.service monitor-standby-symlink.service"
SYSTEMD_AUTO_ENABLE:${PN} = "enable"

FILES:${PN} += " \
    /opt/monitor/scripts/swupdate-check.sh \
    /opt/monitor/scripts/monitor-standby-symlink.sh \
    ${systemd_system_unitdir}/swupdate-check.service \
    ${systemd_system_unitdir}/monitor-standby-symlink.service \
    ${sysconfdir}/swupdate.cfg \
    ${sysconfdir}/swupdate/conf.d/00-home-monitor \
    "

# Cert file only packaged when signing is enabled (ADR-0014)
FILES:${PN}:append = " ${@'${sysconfdir}/swupdate-public.crt' if d.getVar('SWUPDATE_SIGNING') == '1' else ''}"
