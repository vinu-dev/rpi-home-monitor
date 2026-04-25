# =============================================================
# hm-stacks_1.0.bb — Home Monitor add-on container stacks
#
# Ships two systemd units that let the user opt into running
# docker-compose stacks under podman without modifying the core
# image:
#
#   hm-stacks@.service           — template, one instance per stack
#                                  (e.g. `systemctl start hm-stacks@radius`)
#                                  runs `podman compose up -d` against
#                                  /data/stacks/<name>/docker-compose.yml
#
#   hm-stacks-restore.service    — runs once on every boot, scans
#                                  /data/stacks/*/enabled marker files and
#                                  re-enables hm-stacks@<name> for each.
#                                  This survives OTA rootfs swaps which
#                                  wipe systemd's enable symlinks under
#                                  /etc/systemd/system/.
#
# Neither service is enabled by default. Activation flow:
#
#   1. Drop /data/stacks/<name>/docker-compose.yml
#   2. `touch /data/stacks/<name>/enabled`
#   3. `systemctl enable --now hm-stacks-restore.service`
#
# After that, every boot will pick up the stacks listed under /data
# regardless of which rootfs slot is active.
# =============================================================

SUMMARY = "Home Monitor add-on container-stack systemd units"
DESCRIPTION = "Template + restore systemd units for opt-in podman compose stacks"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://hm-stacks@.service \
    file://hm-stacks-restore.service \
    file://hm-stacks-restore.sh \
    file://README.md \
    "

S = "${WORKDIR}"

inherit systemd allarch

RDEPENDS:${PN} = "podman bash"

SYSTEMD_PACKAGES = "${PN}"
# Intentionally empty — neither unit is enabled by default. The user
# opts in by dropping a stack into /data/stacks/<name>/ and then
# `systemctl enable hm-stacks-restore.service`.
SYSTEMD_SERVICE:${PN} = ""

do_install() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/hm-stacks@.service \
        ${D}${systemd_system_unitdir}/hm-stacks@.service
    install -m 0644 ${WORKDIR}/hm-stacks-restore.service \
        ${D}${systemd_system_unitdir}/hm-stacks-restore.service

    install -d ${D}${libexecdir}/hm-stacks
    install -m 0755 ${WORKDIR}/hm-stacks-restore.sh \
        ${D}${libexecdir}/hm-stacks/restore.sh

    install -d ${D}${docdir}/hm-stacks
    install -m 0644 ${WORKDIR}/README.md ${D}${docdir}/hm-stacks/README.md
}

FILES:${PN} = " \
    ${systemd_system_unitdir}/hm-stacks@.service \
    ${systemd_system_unitdir}/hm-stacks-restore.service \
    ${libexecdir}/hm-stacks/restore.sh \
    ${docdir}/hm-stacks/README.md \
    "
