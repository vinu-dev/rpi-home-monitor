# =============================================================
# podman_%.bbappend — Home Monitor storage config for Podman
#
# Pins podman's image / container store to /data/containers so
# pulled images and volumes survive an OTA rootfs A/B swap.
# (/data is on its own partition, mmcblk0p4, mounted via fstab
# entries injected in home-monitor-image.inc.)
#
# This is a .bbappend on meta-virtualization/recipes-containers/podman
# rather than a fresh recipe — we only add a config drop-in, we do not
# repackage podman.
# =============================================================

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI += " \
    file://storage.conf \
    file://hm-containers.tmpfiles.conf \
    "

# Install storage.conf at /etc/containers/storage.conf and the
# tmpfiles fragment so /data/containers is auto-created on first
# boot (the partition exists but the directory may not).
do_install:append() {
    install -d ${D}${sysconfdir}/containers
    install -m 0644 ${WORKDIR}/storage.conf ${D}${sysconfdir}/containers/storage.conf

    install -d ${D}${libdir}/tmpfiles.d
    install -m 0644 ${WORKDIR}/hm-containers.tmpfiles.conf \
        ${D}${libdir}/tmpfiles.d/hm-containers.conf
}

FILES:${PN} += " \
    ${sysconfdir}/containers/storage.conf \
    ${libdir}/tmpfiles.d/hm-containers.conf \
    "
