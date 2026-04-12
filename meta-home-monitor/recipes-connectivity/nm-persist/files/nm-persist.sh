#!/bin/sh
# =============================================================
# nm-persist.sh — Persist NetworkManager connections across OTA
#
# Problem: OTA writes a new rootfs, wiping /etc/NetworkManager/
#          system-connections/. WiFi credentials are lost.
#
# Solution: Store connections on /data (persistent partition),
#           bind-mount over the rootfs directory every boot.
#
# Runs before NetworkManager on every boot:
#   1. If /data/network/system-connections/ is empty, seed from rootfs
#   2. Bind-mount /data/network/ over /etc/NetworkManager/system-connections/
# =============================================================
set -e

PERSIST_DIR="/data/network/system-connections"
ROOTFS_DIR="/etc/NetworkManager/system-connections"

# Wait for /data to be mounted
if ! mountpoint -q /data 2>/dev/null; then
    echo "nm-persist: /data not mounted, skipping"
    exit 0
fi

# Create persistent directory
mkdir -p "$PERSIST_DIR"

# Seed from rootfs on first run (or if persistent dir is empty).
# Skip seeding if .wifi-wiped marker exists (factory reset cleared WiFi
# deliberately — don't restore baked-in connections from the rootfs).
WIPE_MARKER="$PERSIST_DIR/../.wifi-wiped"
if [ -f "$WIPE_MARKER" ]; then
    echo "nm-persist: wifi-wiped marker found — skipping rootfs seed"
    rm -f "$WIPE_MARKER"
elif [ -z "$(ls -A "$PERSIST_DIR" 2>/dev/null)" ]; then
    echo "nm-persist: seeding connections from rootfs"
    if [ -d "$ROOTFS_DIR" ] && [ -n "$(ls -A "$ROOTFS_DIR" 2>/dev/null)" ]; then
        cp -a "$ROOTFS_DIR"/* "$PERSIST_DIR"/
        echo "nm-persist: copied $(ls "$PERSIST_DIR" | wc -l) connection(s)"
    else
        echo "nm-persist: no rootfs connections to seed"
    fi
fi

# Bind-mount persistent dir over rootfs dir
if mountpoint -q "$ROOTFS_DIR" 2>/dev/null; then
    echo "nm-persist: already mounted"
else
    mount --bind "$PERSIST_DIR" "$ROOTFS_DIR"
    echo "nm-persist: bind-mounted $PERSIST_DIR -> $ROOTFS_DIR"
fi
