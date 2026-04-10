#!/bin/sh
# =============================================================
# first-boot-setup.sh — Create /data directory structure
#
# Runs once on first boot. Creates the directory layout on the
# /data partition for recordings, config, certs, and logs.
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

# Set hostname to 'homemonitor' for mDNS — cameras reach server at homemonitor.local
DESIRED_HOSTNAME="homemonitor"
CURRENT_HOSTNAME=$(hostname 2>/dev/null)
if [ "$CURRENT_HOSTNAME" != "$DESIRED_HOSTNAME" ]; then
    echo "Setting hostname: ${CURRENT_HOSTNAME} -> ${DESIRED_HOSTNAME}"
    hostnamectl set-hostname "$DESIRED_HOSTNAME" 2>/dev/null || \
        echo "$DESIRED_HOSTNAME" > /etc/hostname
    echo "Hostname set to ${DESIRED_HOSTNAME} (reachable at ${DESIRED_HOSTNAME}.local)"
fi

# Create directory structure
echo "Creating /data directory structure..."
mkdir -p /data/config
mkdir -p /data/recordings
mkdir -p /data/live
mkdir -p /data/certs
mkdir -p /data/certs/cameras
mkdir -p /data/logs

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
