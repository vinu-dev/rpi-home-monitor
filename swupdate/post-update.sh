#!/bin/sh
# =============================================================
# post-update.sh — SWUpdate post-install script
#
# Switches the active boot slot after rootfs is written, and
# carries network state (WiFi profiles + hostname) from the
# active slot into the freshly-written standby slot so the next
# boot rejoins the LAN instead of factory-resetting into
# setup/AP mode. See ADR-0008 for the persistence contract.
#
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

# SWUpdate invokes shellscript handlers in a sandbox with a stripped
# environment — PATH is not inherited from the parent, so bare command
# names like `mktemp`, `mount`, `fw_setenv`, `fw_printenv` resolve to
# "not found" when the install is driven via the monitor-server API
# (systemd → swupdate → sandbox). Interactive SSH invocations worked
# by accident because PATH was inherited from the login shell. Set a
# conservative PATH here so every code path is identical.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

# Map slot letter → partition that holds that slot's rootfs.
# Layout (both server and camera wks): p2 = slot A, p3 = slot B.
slot_partition() {
    case "$1" in
        A) echo "/dev/mmcblk0p2" ;;
        B) echo "/dev/mmcblk0p3" ;;
        *) return 1 ;;
    esac
}

# Copy files the device needs to stay on the LAN into the new
# slot's rootfs. Pre-OTA this was a silent regression: every
# A/B swap landed a fresh rootfs with only the factory
# `HomeCam-Setup` profile, so devices dropped off WiFi and
# came back in setup/AP mode. Runs best-effort — a failure
# here must not block the update (U-Boot rollback is the
# ultimate safety net if the new slot is unbootable).
carry_network_state() {
    NEW_SLOT="$1"
    NEW_PART="$(slot_partition "$NEW_SLOT")" || {
        echo "carry_network_state: unknown slot '$NEW_SLOT', skipping" >&2
        return 0
    }

    MNT="$(mktemp -d)"
    if ! mount -t ext4 "$NEW_PART" "$MNT" 2>/dev/null; then
        echo "carry_network_state: could not mount $NEW_PART (skipping)" >&2
        rmdir "$MNT" 2>/dev/null || true
        return 0
    fi

    # WiFi connection profiles: copy only real files, never the
    # factory HomeCam-Setup (the new rootfs ships its own copy).
    NM_SRC="/etc/NetworkManager/system-connections"
    NM_DST="$MNT/etc/NetworkManager/system-connections"
    if [ -d "$NM_SRC" ]; then
        mkdir -p "$NM_DST"
        for conn in "$NM_SRC"/*.nmconnection; do
            [ -f "$conn" ] || continue
            base="$(basename "$conn")"
            case "$base" in
                HomeCam-Setup.nmconnection) continue ;;
            esac
            cp -a "$conn" "$NM_DST/$base"
            chmod 600 "$NM_DST/$base" 2>/dev/null || true
            echo "Carried WiFi profile: $base"
        done
    fi

    # Hostname — set during pairing, lives on rootfs, must survive.
    if [ -f /etc/hostname ]; then
        cp -a /etc/hostname "$MNT/etc/hostname" 2>/dev/null || true
    fi

    # Stamp the new rootfs's /etc/sw-versions with the bundle's
    # version. Without this the UI's "Current version" line stays
    # "1.0.0" forever — the Yocto sw-versions recipe bakes that
    # string at build time and nobody else updates it. SWUpdate
    # unpacks sw-description to /tmp/sw-description before invoking
    # handler scripts, so we can parse the `version = "..."` line
    # from there. Preserve whatever component name the image shipped
    # with (typically "home-monitor" or "home-monitor-camera") and
    # rewrite only the version column.
    NEW_VERSION=""
    if [ -f /tmp/sw-description ]; then
        NEW_VERSION=$(sed -n 's/^[[:space:]]*version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
            /tmp/sw-description 2>/dev/null | head -n 1)
    fi
    if [ -n "$NEW_VERSION" ] && [ -f "$MNT/etc/sw-versions" ]; then
        COMPONENT=$(awk 'NR==1 {print $1; exit}' "$MNT/etc/sw-versions" 2>/dev/null)
        COMPONENT=${COMPONENT:-home-monitor}
        printf '%s %s\n' "$COMPONENT" "$NEW_VERSION" > "$MNT/etc/sw-versions"
        echo "Stamped /etc/sw-versions on new slot: $COMPONENT $NEW_VERSION"
    fi

    # /etc/machine-id is intentionally NOT carried: systemd regenerates
    # it on first boot of a fresh rootfs and carrying it can confuse
    # journald / DHCP client identity.

    sync
    umount "$MNT" 2>/dev/null || true
    rmdir "$MNT" 2>/dev/null || true
}

# Derive the standby slot + its partition from the live boot_slot.
# Invariant: the STANDBY slot is the one we are NOT currently booted from,
# so it is always safe to overwrite. Never assume a fixed "devices ship on
# A, always write to B" (earlier `build-swu.sh` hardcoded p3) — that
# silently no-op's once a device has ever been flipped to slot B: the
# bundle rewrites the running partition and U-Boot boots the OLD standby,
# leaving the device on the pre-OTA rootfs. We compute it here instead.
compute_standby() {
    CURRENT_SLOT=$(fw_printenv -n boot_slot 2>/dev/null || echo "A")
    if [ "$CURRENT_SLOT" = "A" ]; then
        NEW_SLOT="B"
    else
        NEW_SLOT="A"
    fi
    NEW_PART="$(slot_partition "$NEW_SLOT")" || {
        echo "compute_standby: unknown slot '$NEW_SLOT'" >&2
        return 1
    }
    export CURRENT_SLOT NEW_SLOT NEW_PART
}

case "$1" in
    preinst)
        # Point the stable name sw-description references at the ACTUAL
        # standby partition for this install. The raw handler then writes
        # to `/dev/monitor_standby` and lands on the correct slot.
        compute_standby || exit 1
        ln -sfn "$NEW_PART" /dev/monitor_standby
        echo "Pre-install: standby slot $NEW_SLOT ($NEW_PART) → /dev/monitor_standby"
        ;;
    postinst)
        compute_standby || exit 1
        echo "Switching boot slot: $CURRENT_SLOT -> $NEW_SLOT"

        # Seed the newly-written rootfs with current network state
        # BEFORE we tell U-Boot to boot it.
        carry_network_state "$NEW_SLOT" || true

        fw_setenv boot_slot "$NEW_SLOT"
        fw_setenv boot_count 0
        fw_setenv upgrade_available 1
        rm -f /dev/monitor_standby 2>/dev/null || true
        echo "Boot environment updated. Reboot to activate."
        ;;
    postfailure)
        rm -f /dev/monitor_standby 2>/dev/null || true
        echo "Install failed — keeping current boot slot"
        ;;
esac
