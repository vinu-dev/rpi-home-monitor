#!/bin/sh
# =============================================================================
# ensure-camera-overlay.sh — Verify config.txt has the camera sensor overlay
#
# Runs early on boot (before camera-streamer) to ensure the RPi firmware
# has loaded the correct device tree overlay for the camera sensor.
#
# This is needed because OTA updates (SWUpdate) only write the rootfs
# partitions (A/B), not the boot partition where config.txt lives.
# After an A/B slot switch, config.txt may be stale if the original
# image was flashed without the overlay or if a different image was
# used for the initial flash.
#
# Idempotent: only writes if the overlay line is missing.
# =============================================================================

set -eu

BOOT_MOUNT="/boot"
BOOT_DEV="/dev/mmcblk0p1"
BOOT_CONFIG="${BOOT_MOUNT}/config.txt"
OVERLAY="dtoverlay=ov5647"
AUTO_DETECT="camera_auto_detect=0"

# Mount boot partition if not already mounted
if ! mountpoint -q "${BOOT_MOUNT}"; then
    mkdir -p "${BOOT_MOUNT}"
    mount -t vfat "${BOOT_DEV}" "${BOOT_MOUNT}" || {
        echo "ensure-camera-overlay: cannot mount ${BOOT_DEV}" >&2
        exit 1
    }
fi

# Check if overlay is already present
if grep -q "^${OVERLAY}$" "${BOOT_CONFIG}" 2>/dev/null; then
    echo "ensure-camera-overlay: ${OVERLAY} already present"
    exit 0
fi

# Remount read-write if needed
mount -o remount,rw "${BOOT_MOUNT}" 2>/dev/null || true

# Add camera overlay settings
{
    echo ""
    echo "# Camera sensor (added by ensure-camera-overlay)"
    grep -q "^${AUTO_DETECT}$" "${BOOT_CONFIG}" 2>/dev/null || echo "${AUTO_DETECT}"
    echo "${OVERLAY}"
} >> "${BOOT_CONFIG}"

# Remount read-only
mount -o remount,ro "${BOOT_MOUNT}" 2>/dev/null || true

echo "ensure-camera-overlay: added ${OVERLAY} to config.txt — reboot required for camera detection"
