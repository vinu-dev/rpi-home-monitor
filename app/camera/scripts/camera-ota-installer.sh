#!/bin/sh
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
#      /var/lib/camera-ota/staging/update.swu
#   2. camera-streamer writes /var/lib/camera-ota/trigger
#   3. camera-ota-installer.path detects the trigger
#   4. camera-ota-installer.service runs THIS script as root
#   5. Progress is reported back via /var/lib/camera-ota/status.json
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

SPOOL=/var/lib/camera-ota
STAGING="$SPOOL/staging"
TRIGGER="$SPOOL/trigger"
STATUS="$SPOOL/status.json"
LOG="$SPOOL/install.log"
PUBKEY_SYSTEM=/etc/swupdate-public.crt
PUBKEY_DATA=/data/certs/swupdate-public.crt

log() {
    printf '%s %s\n' "$(date -Iseconds)" "$*" | tee -a "$LOG"
}

write_status() {
    # $1=state, $2=progress, $3=error
    state="$1"; progress="$2"; error="${3:-}"
    now=$(date +%s)
    # Escape quotes and backslashes in error string for JSON safety.
    error_escaped=$(printf '%s' "$error" | sed 's/\\/\\\\/g; s/"/\\"/g')
    tmp="$STATUS.tmp"
    cat > "$tmp" <<EOF
{"state":"$state","progress":$progress,"error":"$error_escaped","updated_at":$now}
EOF
    mv -f "$tmp" "$STATUS"
    chmod 0664 "$STATUS" 2>/dev/null || true
}

cleanup() {
    rm -f "$TRIGGER" 2>/dev/null || true
}
trap cleanup EXIT

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

# Pick verification cert. Prefer /etc shipped key; fall back to /data
# for dev builds. Absence ⇒ dev/unsigned, allowed by design (ADR-0014).
PUBKEY=""
if [ -f "$PUBKEY_SYSTEM" ]; then
    PUBKEY="$PUBKEY_SYSTEM"
elif [ -f "$PUBKEY_DATA" ]; then
    PUBKEY="$PUBKEY_DATA"
fi

# Phase 1: verify signature (if a key is available).
if [ -n "$PUBKEY" ]; then
    write_status "verifying" 10 ""
    log "Verifying signature with $PUBKEY"
    if ! swupdate -c -i "$BUNDLE" -k "$PUBKEY" >> "$LOG" 2>&1; then
        write_status "error" 10 "Signature verification failed"
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
    log "FAIL: install exit non-zero"
    exit 1
fi

write_status "installed" 100 ""
log "Install complete — reboot required to activate"

# Bundle cleanup: keep only the log (rotated by installer on next run).
rm -f "$BUNDLE" 2>/dev/null || true
exit 0
