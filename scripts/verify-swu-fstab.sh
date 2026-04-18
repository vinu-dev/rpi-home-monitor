#!/usr/bin/env bash
# =============================================================
# verify-swu-fstab.sh — Sanity-check a .swu bundle's fstab
#
# Usage:
#   ./scripts/verify-swu-fstab.sh <path/to/bundle.swu>
#
# Why this exists:
#   A .swu bundle contains rootfs.ext4.gz which becomes the
#   rootfs of slot B after OTA install. If /etc/fstab inside
#   that rootfs is missing /dev/mmcblk0p4 (/data) or
#   /dev/mmcblk0p1 (/boot), the device will boot with /data
#   unmounted — WiFi/LUKS carry-over will silently fail and
#   the device falls back to AP setup mode.
#
#   We learned this the hard way on 2026-04-17: wic injects
#   fstab entries at do_image_wic time, but the OTA bundle
#   uses do_image_ext4 which bypasses that. The fix
#   (ROOTFS_POSTPROCESS_COMMAND in base-files_%.bbappend)
#   must NEVER silently regress — this script gates it.
#
# Exit codes:
#   0 — fstab contains all required entries
#   1 — missing entry, or extraction failed
#
# Requires: cpio, gunzip, debugfs (e2fsprogs). No root needed.
# =============================================================
set -euo pipefail

SWU="${1:-}"
if [ -z "$SWU" ] || [ ! -f "$SWU" ]; then
    echo "Usage: $0 <bundle.swu>"
    exit 1
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo ">>> Extracting $(basename "$SWU") ..."
(cd "$WORK" && cpio -idmu --quiet < "$SWU")

if [ ! -f "$WORK/rootfs.ext4.gz" ]; then
    echo "ERROR: rootfs.ext4.gz not found in bundle"
    exit 1
fi

echo ">>> Decompressing rootfs ..."
gunzip "$WORK/rootfs.ext4.gz"

echo ">>> Reading /etc/fstab from rootfs (via debugfs, no mount) ..."
FSTAB=$(debugfs -R 'cat /etc/fstab' "$WORK/rootfs.ext4" 2>/dev/null || true)

if [ -z "$FSTAB" ]; then
    echo "ERROR: could not read /etc/fstab from rootfs"
    exit 1
fi

echo "--- fstab ---"
echo "$FSTAB"
echo "-------------"

FAIL=0
if ! echo "$FSTAB" | grep -qE '^/dev/mmcblk0p4[[:space:]]+/data'; then
    echo "FAIL: missing /dev/mmcblk0p4 -> /data entry"
    FAIL=1
fi
if ! echo "$FSTAB" | grep -qE '^/dev/mmcblk0p1[[:space:]]+/boot'; then
    echo "FAIL: missing /dev/mmcblk0p1 -> /boot entry"
    FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then
    echo "PASS: fstab has both /data and /boot entries"
    exit 0
fi

echo ""
echo "This bundle will brick OTA. Fix meta-home-monitor/recipes-core/base-files/base-files_%.bbappend"
exit 1
