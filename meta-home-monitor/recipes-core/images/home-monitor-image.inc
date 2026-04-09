# =============================================================
# home-monitor-image.inc — Shared content for RPi 4B server image
# Included by both dev and prod variants.
# =============================================================
SUMMARY = "Raspberry Pi 4B Home Monitoring Server Image"
DESCRIPTION = "Image with video streaming, storage, web interface, \
and security hardening for home camera monitoring."
LICENSE = "MIT"

inherit core-image

# --- Packagegroups ---
IMAGE_INSTALL += " \
    packagegroup-monitor-base \
    packagegroup-monitor-video \
    packagegroup-monitor-web \
    packagegroup-monitor-security \
    "

# --- Storage & filesystem tools ---
IMAGE_INSTALL += " \
    e2fsprogs \
    dosfstools \
    util-linux \
    iptables \
    "

# --- System utilities ---
IMAGE_INSTALL += " \
    wget \
    rsync \
    cronie \
    logrotate \
    "

# --- Application ---
IMAGE_INSTALL += " \
    monitor-server \
    monitor-certs \
    first-boot \
    "

# --- Image output ---
IMAGE_FSTYPES = "wic.bz2 wic.bmap"
WKS_FILE = "home-monitor-ab.wks"
WKS_FILE_DEPENDS = "e2fsprogs-native dosfstools-native mtools-native"

# --- Extra space for video recordings (1GB) ---
IMAGE_ROOTFS_EXTRA_SPACE = "1048576"
