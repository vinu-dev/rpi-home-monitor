# =============================================================
# base-files customization
# =============================================================
# OS branding is handled by os-release.bbappend — do not add
# /etc/os-release here as it conflicts with the os-release package.
#
# /data and /boot fstab entries
# --------------------------------------------------------------
# These entries MUST live in the rootfs itself (not injected by
# wic), because the OTA rootfs image (rootfs.ext4.gz inside a
# .swu) is produced by do_image_ext4 and does NOT go through wic.
# Without these lines, an OTA'd rootfs boots with /data as a
# stub directory on the rootfs overlay — services start with an
# empty /data, LUKS/Network carry-over never runs, and the camera
# falls back to AP setup mode. See ADR-0008.
#
# The append is idempotent: wic may also inject these lines when
# building the initial SD-card image, so we guard with grep.

ROOTFS_POSTPROCESS_COMMAND += "home_monitor_inject_fstab;"

home_monitor_inject_fstab() {
    fstab="${IMAGE_ROOTFS}/etc/fstab"
    if ! grep -q "^/dev/mmcblk0p4" "$fstab"; then
        echo "/dev/mmcblk0p4	/data	ext4	defaults	0	2" >> "$fstab"
    fi
    if ! grep -q "^/dev/mmcblk0p1" "$fstab"; then
        echo "/dev/mmcblk0p1	/boot	vfat	defaults	0	0" >> "$fstab"
    fi
}
