# =============================================================
# base-files customization — add /data partition to fstab
# =============================================================
# OS branding is handled by os-release.bbappend — do not add
# /etc/os-release here as it conflicts with the os-release package.

# Mount the /data partition (label=data) at boot.
# This partition holds recordings, config, certs, and logs.
# It persists across OTA rootfs updates.
do_install:append() {
    echo "LABEL=data  /data  ext4  defaults,noatime  0  2" >> ${D}${sysconfdir}/fstab
}
