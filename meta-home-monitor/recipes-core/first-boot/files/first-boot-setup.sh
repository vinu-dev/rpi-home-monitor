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

echo "Running first boot setup..."

# Create directory structure
mkdir -p /data/config
mkdir -p /data/recordings
mkdir -p /data/live
mkdir -p /data/certs
mkdir -p /data/certs/cameras
mkdir -p /data/logs

# Set ownership — monitor user for server, camera user for camera
if id monitor >/dev/null 2>&1; then
    chown -R monitor:monitor /data/config /data/recordings /data/live /data/logs
    chown -R monitor:monitor /data/certs
fi

if id camera >/dev/null 2>&1; then
    chown -R camera:camera /data/config /data/certs
fi

# Permissions
chmod 750 /data/config /data/certs /data/logs
chmod 755 /data/recordings /data/live

# Mark first boot as done
touch "$STAMP"

echo "First boot setup complete."
