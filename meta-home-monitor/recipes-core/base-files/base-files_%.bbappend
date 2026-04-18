# =============================================================
# base-files customization
# =============================================================
# OS branding is handled by os-release.bbappend — do not add
# /etc/os-release here as it conflicts with the os-release package.
#
# /data and /boot fstab entries
# --------------------------------------------------------------
# These live in the image recipes (home-{monitor,camera}-image.inc)
# via ROOTFS_POSTPROCESS_COMMAND, NOT here. ROOTFS_POSTPROCESS_COMMAND
# additions in a package .bbappend scope to the package's own recipe
# and never reach the image — learned the hard way on 2026-04-18.
# See: meta-home-monitor/recipes-core/images/home-monitor-image.inc
