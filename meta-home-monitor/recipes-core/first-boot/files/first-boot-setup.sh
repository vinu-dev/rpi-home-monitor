#!/bin/sh
# REQ: SWR-021, SWR-049; RISK: RISK-010, RISK-018; SEC: SC-010, SC-019; TEST: TC-021, TC-044
# =============================================================
# first-boot-setup.sh — Create /data directory structure
#
# Runs once on first boot. Expands the data partition to fill
# the SD card, then creates the directory layout on /data for
# recordings, config, certs, and logs.
# =============================================================
set -e

STAMP="/data/.first-boot-done"

mount_source() {
    target="$1"
    if command -v findmnt >/dev/null 2>&1; then
        found=$(findmnt -n -o SOURCE "$target" 2>/dev/null || true)
        if [ -n "$found" ]; then
            echo "$found"
            return 0
        fi
    fi
    awk -v target="$target" '$2 == target { print $1; exit }' /proc/mounts
}

split_partition_device() {
    PART_DISK=""
    PART_NUM=""
    dev="$1"
    case "$dev" in
        /dev/mmcblk*p[0-9]*)
            PART_DISK=$(echo "$dev" | sed 's/p[0-9][0-9]*$//')
            PART_NUM=$(echo "$dev" | sed 's/^.*p//')
            ;;
        /dev/[sv]d*[0-9]*)
            PART_DISK=$(echo "$dev" | sed 's/[0-9][0-9]*$//')
            PART_NUM=$(echo "$dev" | sed 's/^.*[^0-9]//')
            ;;
    esac
}

grow_partition_to_end() {
    disk="$1"
    partnum="$2"
    if [ -z "$disk" ] || [ -z "$partnum" ]; then
        return 0
    fi
    if command -v growpart >/dev/null 2>&1; then
        growpart "$disk" "$partnum" || true
    elif command -v parted >/dev/null 2>&1; then
        # GNU parted asks for confirmation when resizing mounted partitions.
        printf 'Yes\n' | parted ---pretend-input-tty "$disk" resizepart "$partnum" 100% || true
    fi
    partprobe "$disk" 2>/dev/null || true
    blockdev --rereadpt "$disk" 2>/dev/null || true
}

if [ -f "$STAMP" ]; then
    echo "First boot setup already completed."
    exit 0
fi

echo "=== First boot setup starting ==="
echo "Checking /data mount..."
if mountpoint -q /data; then
    echo "/data is mounted"
else
    # Hard fail: proceeding would create the directory structure on
    # the rootfs overlay (/data as a plain directory), which then
    # hides the real data partition when it belatedly mounts, and
    # downstream services (camera-streamer, monitor) see empty
    # state → factory-reset behaviour. See ADR-0008.
    # systemd will treat this as failed and block services that
    # declare `Requires=first-boot-setup.service`.
    echo "ERROR: /data is NOT mounted — refusing to seed directories on rootfs overlay." >&2
    echo "       Fix fstab / partition layout and reboot." >&2
    exit 1
fi

# --- Expand data partition to fill SD card ---
DATA_DEV=$(mount_source /data 2>/dev/null || true)
if [ -n "$DATA_DEV" ]; then
    GROW_DEV="$DATA_DEV"
    RESIZE_DEV="$DATA_DEV"
    if echo "$DATA_DEV" | grep -q '^/dev/mapper/' && command -v cryptsetup >/dev/null 2>&1; then
        MAP_NAME=$(basename "$DATA_DEV")
        BACKING_DEV=$(cryptsetup status "$MAP_NAME" 2>/dev/null | awk '/device:/ { print $2; exit }')
        if [ -n "$BACKING_DEV" ]; then
            GROW_DEV="$BACKING_DEV"
        fi
    fi

    split_partition_device "$GROW_DEV"
    if [ -n "$PART_DISK" ] && [ -n "$PART_NUM" ]; then
        echo "Expanding data partition ${GROW_DEV} (${PART_DISK} part ${PART_NUM})..."

        grow_partition_to_end "$PART_DISK" "$PART_NUM"

        if echo "$DATA_DEV" | grep -q '^/dev/mapper/' && command -v cryptsetup >/dev/null 2>&1; then
            cryptsetup resize "$MAP_NAME" 2>/dev/null || true
        fi

        # Resize filesystem to match partition
        if command -v resize2fs >/dev/null 2>&1; then
            resize2fs "$RESIZE_DEV" 2>/dev/null || true
            NEW_SIZE=$(df -h /data 2>/dev/null | tail -1 | awk '{print $2}')
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
