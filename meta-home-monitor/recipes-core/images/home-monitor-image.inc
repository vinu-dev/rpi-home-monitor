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

# --- OTA update engine (ADR-0008) ---
IMAGE_INSTALL += " \
    swupdate \
    swupdate-progress \
    libubootenv-bin \
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

# --- Container runtime (Podman) for optional add-on stacks ---
# Server-only — gated on virtualization in DISTRO_FEATURES, which is
# set in config/rpi4b/local.conf and NOT in the shared distro config.
# Camera builds therefore never pull these in.
#
# Packages:
#   podman          rootless container engine (no daemon)
#   podman-docker   /usr/bin/docker → podman shim, for compose/CLI parity
#   cni-plugins     bridge/host-local/loopback for default network
#   fuse-overlayfs  rootless overlayfs storage driver
#   slirp4netns     userspace networking for rootless containers
#   conmon          container monitor (runtime helper)
#   crun            OCI runtime (lighter than runc, default for podman)
#   netavark        next-gen container network backend (replaces CNI)
#   aardvark-dns    DNS for netavark networks
#
# hm-stacks systemd units (see recipes-extended/podman/) live with the
# storage-config bbappend and are pulled in via that recipe's RDEPENDS.
IMAGE_INSTALL += " \
    ${@bb.utils.contains('DISTRO_FEATURES', 'virtualization', \
        'podman podman-docker cni-plugins fuse-overlayfs slirp4netns \
         conmon crun netavark aardvark-dns hm-stacks', '', d)} \
    "

# --- Image output ---
IMAGE_FSTYPES = "wic.bz2 wic.bmap ext4.gz"
WKS_FILE = "home-monitor-ab.wks"
WKS_FILE_DEPENDS = "e2fsprogs-native dosfstools-native mtools-native"

# --- Default hostname (overridden by first-boot and provisioning) ---
hostname = "rpi-divinu"

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
# LUKS carry-over silently fails → device drops to AP setup mode.
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

# --- Extra space for video recordings (1GB) + container images (256 MB) ---
# +256 MB headroom on raspberrypi4-64 for podman base images that may be
# baked-in or pre-pulled on first boot. Camera doesn't pull this image
# .inc so its rootfs sizing is unaffected.
IMAGE_ROOTFS_EXTRA_SPACE = "1048576"
IMAGE_ROOTFS_EXTRA_SPACE:append:raspberrypi4-64 = " + 262144"
