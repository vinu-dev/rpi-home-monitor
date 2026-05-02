#!/bin/sh
# REQ: SWR-010, SWR-046; RISK: RISK-004, RISK-019; SEC: SC-003, SC-018; TEST: TC-013, TC-043
# =============================================================
# monitor-standby-symlink.sh — boot-time /dev/monitor_standby
#
# SWUpdate's sw-description references /dev/monitor_standby as
# the install target. SWUpdate's check_free_space runs BEFORE
# any preinst handler, stat()ing the device to decide whether
# the image fits. If the symlink is absent at that moment it
# falls back to the tmpfs of /tmp and reports a false "not
# enough free space" against /tmp's size rather than the real
# partition's 2 GiB.
#
# This script runs once per boot and creates the symlink
# pointing at the STANDBY slot (the one we're NOT booted from),
# so it's safe to overwrite on next OTA. post-update.sh still
# rewrites the symlink in preinst/postinst in case boot_slot
# has flipped mid-boot (it never does in practice, but the
# rewrite is idempotent).
# =============================================================
set -eu

export PATH=/usr/sbin:/usr/bin:/sbin:/bin

CURRENT_SLOT=$(fw_printenv -n boot_slot 2>/dev/null || echo "A")
case "$CURRENT_SLOT" in
    A) NEW_PART=/dev/mmcblk0p3 ;;
    B) NEW_PART=/dev/mmcblk0p2 ;;
    *)
        echo "monitor-standby-symlink: unknown boot_slot=$CURRENT_SLOT" >&2
        exit 1
        ;;
esac

if [ -L /dev/monitor_standby ] && \
   [ "$(readlink /dev/monitor_standby)" = "$NEW_PART" ]; then
    # Already correct; nothing to do.
    exit 0
fi

ln -sfn "$NEW_PART" /dev/monitor_standby
echo "monitor-standby-symlink: /dev/monitor_standby -> $NEW_PART (boot_slot=$CURRENT_SLOT)"
