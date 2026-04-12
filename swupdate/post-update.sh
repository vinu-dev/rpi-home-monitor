#!/bin/sh
# =============================================================
# post-update.sh — SWUpdate post-install script
#
# Switches the active boot slot after rootfs is written.
# U-Boot reads boot_slot to decide which partition to boot.
# upgrade_available=1 tells swupdate-check.sh to run health
# checks and confirm (or let U-Boot rollback after bootlimit).
#
# SWUpdate calls shellscripts at multiple phases:
#   $1 = "preinst"     — before image is written
#   $1 = "postinst"    — after image is written (this is where we act)
#   $1 = "postfailure" — after a failed install (revert slot)
# =============================================================
set -e

case "$1" in
    preinst)
        echo "Pre-install: rootfs will be written to standby slot"
        ;;
    postinst)
        CURRENT_SLOT=$(fw_printenv -n boot_slot 2>/dev/null || echo "A")
        if [ "$CURRENT_SLOT" = "A" ]; then
            NEW_SLOT="B"
        else
            NEW_SLOT="A"
        fi
        echo "Switching boot slot: $CURRENT_SLOT -> $NEW_SLOT"
        fw_setenv boot_slot "$NEW_SLOT"
        fw_setenv boot_count 0
        fw_setenv upgrade_available 1
        echo "Boot environment updated. Reboot to activate."
        ;;
    postfailure)
        echo "Install failed — keeping current boot slot"
        ;;
esac
