#!/bin/sh
# REQ: SWR-038; RISK: RISK-004; SEC: SC-003; TEST: TC-036
# =============================================================
# camera-ota-installer.sh — root-privileged OTA installer
#
# The camera-streamer service runs as the unprivileged `camera`
# user with NoNewPrivileges=true, which means it cannot invoke
# `swupdate -i` directly (swupdate needs root for /dev symlinks,
# ext4 mount of the standby slot for network-state carry-over,
# and fw_setenv to flip the boot slot).
#
# This script is the privileged half of the OTA pipeline:
#
#   1. camera-streamer (user=camera) stages the bundle at
#      /data/ota/camera-spool/staging/update.swu
#   2. camera-streamer writes /data/ota/camera-spool/trigger
#   3. camera-ota-installer.path detects the trigger
#   4. camera-ota-installer.service runs THIS script as root
#   5. Progress is reported back via /data/ota/camera-spool/status.json
#
# State file format (status.json):
#   {"state": "idle|verifying|installing|installed|error",
#    "progress": 0..100,
#    "error": "",
#    "started_at": <unix-ts>,
#    "updated_at": <unix-ts>}
#
# This script is deliberately single-shot: it reads one trigger,
# does one install, cleans up, exits. Path-unit re-arms for the
# next trigger.
# =============================================================
set -eu

export PATH=/usr/sbin:/usr/bin:/sbin:/bin

SPOOL=${CAMERA_OTA_SPOOL_DIR:-/data/ota/camera-spool}
STAGING="$SPOOL/staging"
OTA_TMP="$SPOOL/tmp"
TRIGGER="$SPOOL/trigger"
STATUS="$SPOOL/status.json"
LOG="$SPOOL/install.log"
PUBKEY_SYSTEM=/etc/swupdate-public.crt
PUBKEY_DATA=/data/certs/swupdate-public.crt
LEDCTL=/usr/bin/home-monitor-ledctl
AUTO_REBOOT=0

log() {
    printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG"
}

set_led() {
    if [ -x "$LEDCTL" ]; then
        "$LEDCTL" "$1" --role camera --force >/dev/null 2>&1 || true
    fi
}

status_extra_json() {
    python3 - "$STATUS" <<'PY' 2>/dev/null || true
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    data = {}

extra = {}
for key in ("target_version", "current_version", "update_relation"):
    value = data.get(key)
    if value:
        extra[key] = value
if extra:
    print("," + ",".join(json.dumps(k) + ":" + json.dumps(v) for k, v in extra.items()))
PY
}

status_value() {
    python3 - "$STATUS" "$1" <<'PY' 2>/dev/null || true
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    data = {}
print(data.get(sys.argv[2], ""))
PY
}

write_status() {
    # $1=state, $2=progress, $3=error
    state="$1"; progress="$2"; error="${3:-}"
    now=$(date +%s)
    # Escape quotes and backslashes in error string for JSON safety.
    error_escaped=$(printf '%s' "$error" | sed 's/\\/\\\\/g; s/"/\\"/g')
    extra_json=$(status_extra_json)
    tmp="$STATUS.tmp"
    cat > "$tmp" <<EOF
{"state":"$state","progress":$progress,"error":"$error_escaped","updated_at":$now$extra_json}
EOF
    mv -f "$tmp" "$STATUS"
    chmod 0664 "$STATUS" 2>/dev/null || true
}

cleanup() {
    # Trigger must be removed on every exit — otherwise is_busy() on
    # the camera-streamer side sees it and rejects the next upload
    # with HTTP 409 "Another update is already in progress", even
    # though no install is actually running.
    rm -f "$TRIGGER" 2>/dev/null || true
    # Restart camera-streamer if we stopped it for the install. Variable
    # may be unset if we exit before phase 2 starts.
    if [ "${STREAMER_WAS_ACTIVE:-0}" = "1" ] && [ "${AUTO_REBOOT:-0}" != "1" ]; then
        log "Restarting camera-streamer"
        systemctl start camera-streamer.service 2>/dev/null || true
    fi
}
trap cleanup EXIT

# Ensure the persistent /data-backed spool exists even on older images
# whose tmpfiles rules created the legacy /var/lib path.
mkdir -p "$STAGING" "$OTA_TMP"
chown camera:camera "$SPOOL" "$STAGING" "$OTA_TMP" 2>/dev/null || true
chmod 2775 "$SPOOL" "$STAGING" "$OTA_TMP" 2>/dev/null || true
export TMPDIR="$OTA_TMP"

# Path unit fires on trigger creation. If trigger is missing (race
# with a previous run), nothing to do.
if [ ! -f "$TRIGGER" ]; then
    exit 0
fi

BUNDLE=$(head -n 1 "$TRIGGER" 2>/dev/null || true)
if [ -z "$BUNDLE" ]; then
    BUNDLE="$STAGING/update.swu"
fi

if [ ! -f "$BUNDLE" ]; then
    write_status "error" 0 "Bundle not found: $BUNDLE"
    log "FAIL: bundle $BUNDLE not found"
    exit 1
fi

log "Install requested for $BUNDLE"
set_led ota-installing

# Ensure /dev/monitor_standby is pointing at the STANDBY partition
# regardless of what monitor-standby-symlink.service did at boot.
# SWUpdate's check_free_space stats this device before any preinst
# can run — a missing or wrong symlink silently rejects the install
# with a "not enough free space" error against /tmp's tmpfs. We've
# seen the boot-time service occasionally report boot_slot=B even on
# a slot-A boot (likely a race with /boot mount order), so always
# refresh here too.
BOOT_SLOT=$(fw_printenv -n boot_slot 2>/dev/null || echo A)
case "$BOOT_SLOT" in
    A) STANDBY=/dev/mmcblk0p3 ;;
    B) STANDBY=/dev/mmcblk0p2 ;;
    *) log "FAIL: unknown boot_slot=$BOOT_SLOT"; write_status error 0 "Unknown boot_slot=$BOOT_SLOT"; exit 1 ;;
esac
ln -sfn "$STANDBY" /dev/monitor_standby
log "Standby symlink: /dev/monitor_standby -> $STANDBY (boot_slot=$BOOT_SLOT)"

if ! command -v swupdate >/dev/null 2>&1; then
    write_status "error" 0 "swupdate is not installed"
    log "FAIL: swupdate binary not found"
    exit 1
fi

# Pick verification cert. Prefer /etc shipped key; fall back to /data
# for dev builds. Absence ⇒ dev/unsigned, allowed by design (ADR-0014)
# UNLESS the image was built with SWUPDATE_SIGNING=1 (bbappend drops
# /etc/swupdate-enforce as a marker). If enforcement is on but the
# cert is missing at runtime, refuse to install rather than silently
# accept anything.
PUBKEY=""
if [ -f "$PUBKEY_SYSTEM" ]; then
    PUBKEY="$PUBKEY_SYSTEM"
elif [ -f "$PUBKEY_DATA" ]; then
    PUBKEY="$PUBKEY_DATA"
fi
if [ -z "$PUBKEY" ] && [ -f /etc/swupdate-enforce ]; then
    write_status "error" 0 "Signature enforcement on but cert missing — re-flash a signed image"
    log "FAIL: enforce marker present but no public cert found at $PUBKEY_SYSTEM or $PUBKEY_DATA"
    exit 1
fi

# Zero the first 16 MB of the standby partition before swupdate
# writes. If a previous install was interrupted (OOM kill, power
# loss, bad bundle) the partition can end up with a "looks mounted
# but every inode is corrupt" filesystem. swupdate's raw write will
# overwrite it anyway, but post-update.sh's postinst mounts the
# freshly-written partition to carry the WiFi profile over — and
# if that mount hits a stale superblock on top of a half-written
# image, lookups flood the kernel log with EXT4 checksum errors
# and systemd-userwor gets stuck walking the mount point,
# eventually taking sshd + getty + camera-streamer down with it.
# Zeroing the superblock guarantees the post-swupdate mount is of
# the NEW filesystem, not the ghost of a broken one.
log "Zeroing standby partition superblock to clear any stale FS"
dd if=/dev/zero of="$STANDBY" bs=1048576 count=16 2>&1 | tee -a "$LOG" || true
sync

# Phase 1: verify signature (if a key is available).
if [ -n "$PUBKEY" ]; then
    write_status "verifying" 10 ""
    log "Verifying signature with $PUBKEY"
    if ! swupdate -c -i "$BUNDLE" -k "$PUBKEY" >> "$LOG" 2>&1; then
        write_status "error" 10 "Signature verification failed"
        set_led error
        log "FAIL: signature check"
        exit 1
    fi
    log "Signature OK"
else
    log "No public key present — skipping signature check (dev build)"
fi

# Phase 2: install.
write_status "installing" 30 ""
log "Running swupdate -i $BUNDLE"

# Stop camera-streamer to free RAM before the heavy write phase.
# On a Pi Zero 2W (362 MB total) the overlap of camera-streamer
# (~90 MB), swupdate (~250 MB RSS), and kernel buffers during the
# 1.8 GB raw write pushes the kernel into OOM territory — we've seen
# camera-streamer, sshd, and getty all killed mid-install, leaving
# the box unreachable until a physical power cycle.
#
# A trap guarantees we restart it even if swupdate or the script
# fails, so a bad install doesn't leave the device permanently
# without its main service.
STREAMER_WAS_ACTIVE=0
if systemctl is-active --quiet camera-streamer.service; then
    STREAMER_WAS_ACTIVE=1
    log "Stopping camera-streamer to free RAM during install"
    systemctl stop camera-streamer.service || true
fi
# Note: camera-streamer restart + trigger cleanup are handled by the
# single cleanup() trap installed earlier — do NOT add a second
# `trap ... EXIT` here, it would replace the first handler and leave
# the trigger file behind, causing HTTP 409 on the next upload.

# Stream swupdate output to the log while installing. A broad progress
# value of 60 is reported during install; SWUpdate itself is the slow
# part and we can't easily parse its IPC socket from /bin/sh.
if [ -n "$PUBKEY" ]; then
    swupdate -v -i "$BUNDLE" -k "$PUBKEY" >> "$LOG" 2>&1 &
else
    swupdate -v -i "$BUNDLE" >> "$LOG" 2>&1 &
fi
SWU_PID=$!

# Coarse progress ticker while swupdate runs.
progress=30
while kill -0 "$SWU_PID" 2>/dev/null; do
    progress=$((progress + 5))
    [ "$progress" -gt 90 ] && progress=90
    write_status "installing" "$progress" ""
    sleep 3
done

if ! wait "$SWU_PID"; then
    # Grab the last meaningful error line from the log.
    err=$(tail -n 5 "$LOG" 2>/dev/null | grep -iE 'error|fail' | tail -n 1 || true)
    [ -z "$err" ] && err="swupdate returned non-zero"
    write_status "error" 90 "$err"
    set_led error
    log "FAIL: install exit non-zero"
    exit 1
fi

TARGET_VERSION=$(status_value target_version)
AUTO_REBOOT=1
if [ -x "$LEDCTL" ]; then
    "$LEDCTL" ota-rebooting --role camera --force --mark-activation \
        --target-version "$TARGET_VERSION" >/dev/null 2>&1 || true
fi
write_status "rebooting" 100 ""
log "Install complete — rebooting to activate"

# Bundle cleanup: keep only the log (rotated by installer on next run).
rm -f "$BUNDLE" 2>/dev/null || true

# Remove the trigger before reboot so a failed boot does not loop installs.
rm -f "$TRIGGER" 2>/dev/null || true

# Give the GUI/server a brief chance to observe "rebooting" through the normal
# camera status endpoints. The server still handles the network-drop case.
if [ "${STREAMER_WAS_ACTIVE:-0}" = "1" ]; then
    log "Restarting camera-streamer briefly to publish rebooting state"
    systemctl start camera-streamer.service 2>/dev/null || true
    STREAMER_WAS_ACTIVE=0
fi
sleep 3
systemctl reboot
exit 0
