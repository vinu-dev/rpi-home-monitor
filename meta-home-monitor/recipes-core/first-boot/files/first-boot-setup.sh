#!/bin/sh
# =============================================================
# first-boot-setup.sh — Create /data directory structure
#
# Runs once on first boot. Expands the data partition to fill
# the SD card, then creates the directory layout on /data for
# recordings, config, certs, and logs.
# =============================================================
set -e

STAMP="/data/.first-boot-done"

if [ -f "$STAMP" ]; then
    echo "First boot setup already completed."
    exit 0
fi

echo "=== First boot setup starting ==="
echo "Checking /data mount..."
if mountpoint -q /data; then
    echo "/data is mounted"
else
    echo "WARNING: /data is NOT mounted — dirs will be on rootfs"
fi

# --- Expand data partition to fill SD card ---
DATA_DEV=$(findmnt -n -o SOURCE /data 2>/dev/null || true)
if [ -n "$DATA_DEV" ]; then
    # Get the disk and partition number (e.g., /dev/mmcblk0p4 -> /dev/mmcblk0, 4)
    DISK=$(echo "$DATA_DEV" | sed 's/p[0-9]*$//')
    PARTNUM=$(echo "$DATA_DEV" | grep -o '[0-9]*$')

    if [ -n "$DISK" ] && [ -n "$PARTNUM" ]; then
        echo "Expanding data partition ${DATA_DEV} (${DISK} part ${PARTNUM})..."

        # Grow partition to fill remaining disk space
        if command -v growpart >/dev/null 2>&1; then
            growpart "$DISK" "$PARTNUM" || true
        elif command -v parted >/dev/null 2>&1; then
            # parted -s won't auto-confirm on mounted partitions;
            # pipe Yes to ---pretend-input-tty to handle the prompt.
            echo Yes | parted ---pretend-input-tty "$DISK" resizepart "$PARTNUM" 100% || true
            partprobe "$DISK" 2>/dev/null || true
        fi

        # Resize filesystem to match partition
        if command -v resize2fs >/dev/null 2>&1; then
            resize2fs "$DATA_DEV" 2>/dev/null || true
            NEW_SIZE=$(df -h "$DATA_DEV" 2>/dev/null | tail -1 | awk '{print $2}')
            echo "Data partition expanded to ${NEW_SIZE}"
        fi
    fi
fi

# Set hostname — server gets a fixed name, camera gets serial suffix later.
# Camera hostname is set by wifi_setup.py (_set_unique_hostname) during
# first-boot provisioning, so we only set it here for the server.
if id monitor >/dev/null 2>&1; then
    DESIRED_HOSTNAME="rpi-divinu"
    CURRENT_HOSTNAME=$(hostname 2>/dev/null)
    if [ "$CURRENT_HOSTNAME" != "$DESIRED_HOSTNAME" ]; then
        echo "Setting hostname: ${CURRENT_HOSTNAME} -> ${DESIRED_HOSTNAME}"
        hostnamectl set-hostname "$DESIRED_HOSTNAME" 2>/dev/null || \
            echo "$DESIRED_HOSTNAME" > /etc/hostname
        if command -v systemctl >/dev/null 2>&1; then
            systemctl restart avahi-daemon 2>/dev/null || true
        fi
        if command -v nmcli >/dev/null 2>&1; then
            nmcli general hostname "$DESIRED_HOSTNAME" 2>/dev/null || true
        fi
        echo "Hostname set to ${DESIRED_HOSTNAME} (reachable at ${DESIRED_HOSTNAME}.local)"
    fi
else
    echo "Camera board — hostname will be set during WiFi provisioning"
fi

# Create directory structure
echo "Creating /data directory structure..."
mkdir -p /data/config
mkdir -p /data/recordings
mkdir -p /data/live
mkdir -p /data/certs
mkdir -p /data/certs/cameras
mkdir -p /data/logs
mkdir -p /data/tailscale

# Set ownership — monitor user for server, camera user for camera
if id monitor >/dev/null 2>&1; then
    echo "Setting ownership for monitor user (server)"
    chown monitor:monitor /data
    chown -R monitor:monitor /data/config /data/recordings /data/live /data/logs
    chown -R monitor:monitor /data/certs
fi

if id camera >/dev/null 2>&1; then
    echo "Setting ownership for camera user (camera)"
    chown camera:camera /data
    chown -R camera:camera /data/config /data/certs /data/logs
fi

# Permissions
chmod 755 /data
chmod 750 /data/config /data/certs /data/logs
chmod 755 /data/recordings /data/live

# Mark first boot as done
touch "$STAMP"

echo "=== First boot setup complete ==="
echo "  /data/config      — app configuration"
echo "  /data/certs       — TLS certificates"
echo "  /data/recordings  — video clips"
echo "  /data/live        — HLS live segments"
echo "  /data/logs        — app logs"
echo "  /data/tailscale   — VPN state"
