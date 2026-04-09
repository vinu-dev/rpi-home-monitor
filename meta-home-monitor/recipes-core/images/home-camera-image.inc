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
IMAGE_FSTYPES = "wic.bz2 wic.bmap"
WKS_FILE = "home-camera-ab.wks"
WKS_FILE_DEPENDS = "e2fsprogs-native dosfstools-native mtools-native"

# --- Keep image small ---
IMAGE_ROOTFS_EXTRA_SPACE = "262144"
