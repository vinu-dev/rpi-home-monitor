#!/bin/sh
# =============================================================================
# ensure-camera-overlay.sh — Reconcile /boot/config.txt with sensor policy
#
# Runs early on boot (before camera-streamer) so the firmware has the right
# camera sensor configuration regardless of which image originally flashed
# the boot partition.
#
# Background: SWUpdate writes only the rootfs A/B partitions; /boot is never
# touched by an OTA. So a camera updated from an older image carries that
# image's /boot/config.txt forever unless something on the rootfs reconciles
# it. This script is that something.
#
# Default policy: ``camera_auto_detect=1`` and no explicit ``dtoverlay=<sensor>``.
# The firmware probes the CSI bus and picks whichever sensor is connected —
# OV5647, IMX219, IMX477, IMX708 all work out of the box.
#
# Override: if ``/data/config/camera-sensor`` exists and contains a recognised
# sensor name, the script pins that overlay explicitly (``camera_auto_detect=0``
# + ``dtoverlay=<name>``). The file lives on /data so it survives OTAs.
#
# Idempotent: running multiple times against any input leaves the file in
# the same final state. Running against a clean image is a no-op.
# =============================================================================

set -eu

BOOT_MOUNT="${BOOT_MOUNT:-/boot}"
BOOT_DEV="${BOOT_DEV:-/dev/mmcblk0p1}"
BOOT_CONFIG="${BOOT_CONFIG:-${BOOT_MOUNT}/config.txt}"
SENSOR_OVERRIDE_FILE="${SENSOR_OVERRIDE_FILE:-/data/config/camera-sensor}"

# Sensors with overlays shipped in the image. Add to this list (and to
# RPI_KERNEL_DEVICETREE_OVERLAYS in the machine conf) when adding hardware
# support for a new sensor.
SUPPORTED_SENSORS="ov5647 imx219 imx477 imx708"

# Marker tag included in every line this script writes/comments, so a
# follow-up run can recognise its own work and stay idempotent.
MARKER="ensure-camera-overlay"

log() {
    echo "${MARKER}: $*"
}

# True if $1 is in the space-separated SUPPORTED_SENSORS list.
is_supported_sensor() {
    case " ${SUPPORTED_SENSORS} " in
        *" $1 "*) return 0 ;;
        *) return 1 ;;
    esac
}

# Read /data/config/camera-sensor, trim whitespace, lowercase. Empty if
# missing or unrecognised.
read_sensor_override() {
    if [ ! -f "${SENSOR_OVERRIDE_FILE}" ]; then
        return 0
    fi
    raw=$(tr -d '[:space:]' < "${SENSOR_OVERRIDE_FILE}" 2>/dev/null | tr '[:upper:]' '[:lower:]')
    if [ -z "${raw}" ]; then
        return 0
    fi
    if is_supported_sensor "${raw}"; then
        echo "${raw}"
    else
        log "ignoring unrecognised override '${raw}' (supported: ${SUPPORTED_SENSORS})" >&2
    fi
}

# Reconcile the camera lines in $BOOT_CONFIG against the desired state.
# Desired state:
#   - if $1 is empty (auto-detect): exactly one active "camera_auto_detect=1"
#     line; no active "camera_auto_detect=0", no active "dtoverlay=<sensor>"
#   - if $1 is a sensor name (override): exactly one active
#     "camera_auto_detect=0" and one active "dtoverlay=<sensor>"; no other
#     active sensor overlays, no active camera_auto_detect=1
#
# All disabled lines are kept (commented out, with a marker) so an operator
# inspecting /boot/config.txt sees the history.
reconcile() {
    desired_sensor="$1"
    tmp=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f '${tmp}'" EXIT

    # Strip ALL camera-streamer-managed lines from the working copy AND any
    # trailing blank lines, in one pass. We re-emit the canonical block at
    # the end. Lines already commented out by an earlier run carry the
    # MARKER; lines from older Yocto bakes or from the original
    # ensure-camera-overlay.sh do not — the regex matches both forms.
    awk -v marker="${MARKER}" '
        # Active or marker-commented "camera_auto_detect=N" (any leading
        # whitespace, optional "#", any whitespace before the keyword).
        /^[[:space:]]*#?[[:space:]]*camera_auto_detect=[0-9]+/ { next }
        # Active or marker-commented "dtoverlay=<sensor>" — restricted to
        # the sensor names we manage so unrelated dtoverlay lines
        # (vc4-fkms-v3d, act-led, ...) are preserved.
        /^[[:space:]]*#?[[:space:]]*dtoverlay=(ov5647|imx219|imx477|imx708)/ { next }
        # Section header the original ensure-camera-overlay.sh appended.
        /^[[:space:]]*#[[:space:]]*Camera sensor \(added by ensure-camera-overlay\)/ { next }
        # Section header this version appends (substring match — matches
        # whatever marker text we use).
        index($0, "# Camera sensor (managed by " marker ")") { next }
        { lines[++n] = $0 }
        END {
            # Drop trailing blank lines so the file does not grow on every run.
            while (n > 0 && lines[n] ~ /^[[:space:]]*$/) n--
            for (i = 1; i <= n; i++) print lines[i]
        }
    ' "${BOOT_CONFIG}" > "${tmp}"

    # Append the canonical managed block.
    {
        # Single trailing newline before the block, regardless of input shape
        echo ""
        echo "# Camera sensor (managed by ${MARKER})"
        if [ -z "${desired_sensor}" ]; then
            echo "camera_auto_detect=1"
        else
            echo "camera_auto_detect=0"
            echo "dtoverlay=${desired_sensor}"
        fi
    } >> "${tmp}"

    # Compare. If the desired state already matches, do not touch the boot
    # partition — keeps the script a true no-op on healthy images.
    if cmp -s "${tmp}" "${BOOT_CONFIG}"; then
        log "config.txt already in desired state — no changes"
        return 0
    fi

    # Mount /boot rw, write, sync, mount ro.
    if ! mountpoint -q "${BOOT_MOUNT}"; then
        mkdir -p "${BOOT_MOUNT}"
        mount -t vfat "${BOOT_DEV}" "${BOOT_MOUNT}" || {
            log "cannot mount ${BOOT_DEV} on ${BOOT_MOUNT}" >&2
            return 1
        }
    fi
    mount -o remount,rw "${BOOT_MOUNT}" 2>/dev/null || true
    cp "${tmp}" "${BOOT_CONFIG}"
    sync
    mount -o remount,ro "${BOOT_MOUNT}" 2>/dev/null || true
    log "config.txt updated (desired_sensor='${desired_sensor:-auto}')"
}

# Self-test mode: when invoked with --self-test <fixture-file>, treat the
# fixture as $BOOT_CONFIG and never touch a real boot partition. Used by
# the integration test in app/camera/tests/integration/test_ensure_camera_overlay.py.
if [ "${1:-}" = "--self-test" ]; then
    [ -n "${2:-}" ] || { echo "usage: $0 --self-test <config.txt>" >&2; exit 2; }
    BOOT_CONFIG="$2"
    BOOT_MOUNT=$(dirname "$2")
    SENSOR_OVERRIDE_FILE="${HM_OVERRIDE_FILE:-/dev/null}"
    # In self-test we skip the real mount cycle; reconcile() must still
    # see "$BOOT_MOUNT mounted" — fake it by making mountpoint(1) succeed.
    mountpoint() { return 0; }
    mount() { return 0; }
    sync() { return 0; }
fi

override=$(read_sensor_override || true)
reconcile "${override}"
