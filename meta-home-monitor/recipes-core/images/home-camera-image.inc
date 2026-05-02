# REQ: SWR-046, SWR-049, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044, TC-047
# =============================================================
# home-camera-image.inc — Shared content for RPi Zero 2W camera image
# Included by both dev and prod variants.
# =============================================================
SUMMARY = "Raspberry Pi Zero 2W Camera Node Image"
DESCRIPTION = "Minimal image that captures video from the PiHut ZeroCam \
and streams it via RTSPS to the home monitoring server."
LICENSE = "MIT"

inherit core-image

# --- Packagegroups ---
IMAGE_INSTALL += " \
    packagegroup-monitor-base \
    packagegroup-camera-video \
    packagegroup-monitor-security \
    "

# --- OTA update engine (ADR-0008) ---
IMAGE_INSTALL += " \
    swupdate \
    swupdate-progress \
    libubootenv-bin \
    "

# --- Python for camera streamer app ---
IMAGE_INSTALL += " \
    python3 \
    "

# --- Application ---
IMAGE_INSTALL += " \
    camera-streamer \
    first-boot \
    "

# --- Image output ---
IMAGE_FSTYPES = "wic.bz2 wic.bmap ext4.gz"
WKS_FILE = "home-camera-ab.wks"
WKS_FILE_DEPENDS = "e2fsprogs-native dosfstools-native mtools-native"

# --- Default hostname (suffixed with CPU serial by wifi_setup on first boot) ---
hostname = "rpi-divinu-cam"

# Write hostname into /etc/hostname at image creation time.
# (base-files defaults to MACHINE; this override ensures the image
# ships with our product hostname out of the box.)
set_product_hostname() {
    echo "${hostname}" > ${IMAGE_ROOTFS}${sysconfdir}/hostname
}
ROOTFS_POSTPROCESS_COMMAND += "set_product_hostname;"

# --- /data + /boot fstab entries (ADR-0008) -------------------
# Must be in the image rootfs (not wic-injected) so the OTA'd
# slot also carries them. do_image_ext4 bypasses wic, so if these
# are missing the new slot boots without /data mounted and WiFi/
# LUKS carry-over silently fails → camera drops to AP setup mode.
# Idempotent: wic may append the same lines for the SD-card image.
home_monitor_inject_fstab() {
    fstab="${IMAGE_ROOTFS}/etc/fstab"
    if ! grep -q "^/dev/mmcblk0p4" "$fstab"; then
        echo "/dev/mmcblk0p4	/data	ext4	defaults	0	2" >> "$fstab"
    fi
    if ! grep -q "^/dev/mmcblk0p1" "$fstab"; then
        echo "/dev/mmcblk0p1	/boot	vfat	defaults	0	0" >> "$fstab"
    fi
}
ROOTFS_POSTPROCESS_COMMAND += "home_monitor_inject_fstab;"

# --- Keep image small ---
IMAGE_ROOTFS_EXTRA_SPACE = "262144"
