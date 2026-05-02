#!/bin/sh
# REQ: SWR-021, SWR-049; RISK: RISK-010, RISK-018; SEC: SC-010, SC-019; TEST: TC-021, TC-044
# =============================================================
# luks-first-boot.sh — LUKS encrypt /data partition (ADR-0010)
#
# Production images only. Runs on first boot before the main
# first-boot-setup.sh (which creates directory structure on /data).
#
# Server: prompts user for passphrase via systemd-ask-password
# Camera: derives key from pairing_secret + CPU serial via HKDF
#
# Cipher: xchacha20,aes-adiantum-plain64 (2-3.5x faster than AES
# on ARM without hardware acceleration)
#
# UX — LED feedback (issue hotspot-stuck):
#   Set ACT LED to fast-blink while cryptsetup runs (up to several
#   minutes on a Zero 2W). If this script fails silently the user
#   just sees solid green, which is indistinguishable from "stuck
#   kernel default" and led them to think the device was bricked.
#
# UX — fresh-camera fallback:
#   Camera LUKS is keyed off the pairing_secret, but a freshly
#   flashed SD card isn't paired yet — there's no secret. Rather
#   than silently exit and leave /data as a raw partition (which
#   makes local-fs.target hang forever and the setup hotspot never
#   fire), we mkfs.ext4 the partition as a plain filesystem so
#   boot can complete, the hotspot can come up, and the user can
#   pair. Pairing writes a re-encryption marker that a later
#   boot converts to LUKS once the secret exists.
# =============================================================
set -e

STAMP="/data/.luks-done"
DATA_DEV="/dev/mmcblk0p4"
DM_NAME="data"
MOUNT_POINT="/data"

# --- LED control (ACT LED on RPi) ---
LED_PATH="/sys/class/leds/ACT"

led_write() {
    # Best-effort: chmod may fail in a chroot test env.
    [ -w "${LED_PATH}/$1" ] 2>/dev/null && echo "$2" > "${LED_PATH}/$1" 2>/dev/null
    return 0
}

led_working() {
    # Fast blink (200ms on/off) — "I'm busy, don't unplug me".
    # The camera-hotspot.sh / monitor-hotspot.sh set slow blink
    # (1s) for setup mode, so fast blink distinguishes early
    # first-boot work from setup-waiting.
    chmod 0666 "${LED_PATH}/trigger" "${LED_PATH}/brightness" \
        "${LED_PATH}/delay_on" "${LED_PATH}/delay_off" 2>/dev/null || true
    led_write trigger timer
    led_write delay_on 200
    led_write delay_off 200
}

led_off() {
    led_write trigger none
    led_write brightness 0
}

led_working

log() {
    logger -t "luks-first-boot" "$1"
    echo "luks-first-boot: $1"
}

# --- Detect device type ---
is_server() {
    # Server has monitor-server package installed
    [ -f /opt/monitor/monitor/__init__.py ] || \
    systemctl list-unit-files monitor.service >/dev/null 2>&1
}

is_camera() {
    [ -f /opt/camera/camera_streamer/__init__.py ] || \
    systemctl list-unit-files camera-streamer.service >/dev/null 2>&1
}

# --- Check if already LUKS-formatted ---
if cryptsetup isLuks "$DATA_DEV" 2>/dev/null; then
    log "Data partition is already LUKS-encrypted"

    # Open and mount if not already mounted
    if ! mountpoint -q "$MOUNT_POINT"; then
        if [ ! -e "/dev/mapper/$DM_NAME" ]; then
            # Try keyfile first (camera auto-unlock)
            if [ -f /etc/cryptsetup-keys.d/data.key ]; then
                log "Unlocking with keyfile..."
                cryptsetup luksOpen "$DATA_DEV" "$DM_NAME" \
                    --key-file /etc/cryptsetup-keys.d/data.key
            else
                log "Waiting for passphrase to unlock /data..."
                systemd-ask-password --timeout=0 "Enter passphrase for /data:" | \
                    cryptsetup luksOpen "$DATA_DEV" "$DM_NAME"
            fi
        fi
        mount /dev/mapper/"$DM_NAME" "$MOUNT_POINT"
        log "Mounted encrypted /data"
    fi
    led_off
    exit 0
fi

# --- First-time LUKS formatting ---
log "=== LUKS first-boot encryption starting ==="
log "Device: $DATA_DEV"

# Unmount if currently mounted as plain ext4 (from WKS)
if mountpoint -q "$MOUNT_POINT"; then
    umount "$MOUNT_POINT" || true
fi

if is_server; then
    log "Server mode: requesting passphrase for disk encryption"

    # UX — headless first boot:
    #   If nobody is attached to a console (no keyboard + display
    #   and no serial typing), timeout=0 would block forever and
    #   the setup hotspot (which the user DOES expect to see)
    #   never fires. Retry with a 5-minute timeout, then fall
    #   back to plain ext4 + a stamp the admin UI surfaces so
    #   the user can opt into LUKS later from the Settings page.
    PASSPHRASE=""
    PASSPHRASE_TIMEOUT=300
    MAX_RETRIES=5
    RETRY=0
    while [ -z "$PASSPHRASE" ] && [ "$RETRY" -lt "$MAX_RETRIES" ]; do
        RETRY=$((RETRY + 1))
        PASSPHRASE=$(systemd-ask-password --timeout="$PASSPHRASE_TIMEOUT" \
            "Create encryption passphrase for /data (min 12 chars):" || true)

        # Timeout / empty entry — user isn't at a console. Fall back
        # to plain ext4 so the hotspot can come up. Admin opts into
        # LUKS later from Settings -> Storage.
        if [ -z "$PASSPHRASE" ]; then
            log "No passphrase entered within ${PASSPHRASE_TIMEOUT}s — falling back to plain ext4"
            log "The hotspot will come up so the admin can finish setup over WiFi."
            log "Enable LUKS later from Settings -> Storage once the server is reachable."
            if ! blkid -o value -s TYPE "$DATA_DEV" 2>/dev/null | grep -q ext4; then
                mkfs.ext4 -F -L data "$DATA_DEV"
            fi
            mount "$DATA_DEV" "$MOUNT_POINT"
            touch "$MOUNT_POINT/.luks-opt-in-pending"
            led_off
            exit 0
        fi

        # Check minimum length — loop back for another try.
        if [ "${#PASSPHRASE}" -lt 12 ]; then
            log "Passphrase too short (minimum 12 characters) — try again"
            PASSPHRASE=""
            continue
        fi

        # Confirm
        CONFIRM=$(systemd-ask-password --timeout="$PASSPHRASE_TIMEOUT" \
            "Confirm encryption passphrase:" || true)
        if [ "$PASSPHRASE" != "$CONFIRM" ]; then
            log "Passphrases do not match — try again"
            PASSPHRASE=""
        fi
    done

    if [ -z "$PASSPHRASE" ]; then
        log "Exhausted $MAX_RETRIES attempts — falling back to plain ext4 (same as timeout path)"
        if ! blkid -o value -s TYPE "$DATA_DEV" 2>/dev/null | grep -q ext4; then
            mkfs.ext4 -F -L data "$DATA_DEV"
        fi
        mount "$DATA_DEV" "$MOUNT_POINT"
        touch "$MOUNT_POINT/.luks-opt-in-pending"
        led_off
        exit 0
    fi

    log "Formatting $DATA_DEV with LUKS2 + Adiantum (server parameters)..."
    echo -n "$PASSPHRASE" | cryptsetup luksFormat --type luks2 \
        --cipher xchacha20,aes-adiantum-plain64 \
        --hash sha256 \
        --key-size 256 \
        --pbkdf argon2id \
        --pbkdf-memory 1048576 \
        --pbkdf-force-iterations 4 \
        --pbkdf-parallel 4 \
        --batch-mode \
        --key-file=- \
        "$DATA_DEV"

    # Open the LUKS container
    echo -n "$PASSPHRASE" | cryptsetup luksOpen "$DATA_DEV" "$DM_NAME" --key-file=-

    # Clear passphrase from memory
    PASSPHRASE=""

    log "Server LUKS formatting complete"

elif is_camera; then
    log "Camera mode: deriving LUKS key from pairing secret"

    # Camera uses HKDF-derived key — needs pairing_secret
    PAIRING_SECRET_FILE="/data/config/pairing_secret"

    # On first boot after pairing, the pairing_secret is stored by the
    # PairingManager. But /data isn't formatted yet, so the secret was
    # written to a temporary location by the pairing exchange.
    if [ -f /tmp/pairing_secret ]; then
        PAIRING_SECRET_FILE="/tmp/pairing_secret"
    elif [ -f /etc/pairing_secret ]; then
        PAIRING_SECRET_FILE="/etc/pairing_secret"
    fi

    if [ ! -f "$PAIRING_SECRET_FILE" ]; then
        log "No pairing_secret found — camera not yet paired with a server"
        log "Formatting /data as plain ext4 so the setup hotspot can run."
        log "Once paired, the camera re-keys /data to LUKS on the next boot"
        log "(see /data/.luks-migrate-pending marker written at pair time)."

        # Important: the raw partition from the LUKS wks has no
        # filesystem. Without mkfs here, systemd's fsck for
        # /dev/mmcblk0p4 fails -> local-fs.target never fires ->
        # camera-hotspot.service (After=local-fs.target) stays
        # queued forever and the user sees no setup WiFi.
        if ! blkid -o value -s TYPE "$DATA_DEV" 2>/dev/null | grep -q ext4; then
            log "mkfs.ext4 -L data $DATA_DEV"
            mkfs.ext4 -F -L data "$DATA_DEV"
        fi

        mount "$DATA_DEV" "$MOUNT_POINT" || {
            log "ERROR: mkfs succeeded but mount failed on $DATA_DEV"
            led_off
            exit 1
        }
        log "Plain ext4 mounted at $MOUNT_POINT — boot can proceed"
        led_off
        exit 0
    fi

    # Derive key using HKDF-SHA256 (camera_streamer.encryption module)
    DERIVED_KEY=$(python3 -c "
import sys
sys.path.insert(0, '/opt/camera')
from camera_streamer.encryption import hkdf_sha256, get_cpu_serial, HKDF_INFO, KEY_LENGTH
secret_hex = open('$PAIRING_SECRET_FILE').read().strip()
ikm = bytes.fromhex(secret_hex)
salt = get_cpu_serial().encode('utf-8')
if not salt:
    print('ERROR: no CPU serial', file=sys.stderr)
    sys.exit(1)
sys.stdout.buffer.write(hkdf_sha256(ikm, salt, HKDF_INFO, KEY_LENGTH))
" 2>/dev/null) || {
        log "ERROR: Key derivation failed"
        mount "$DATA_DEV" "$MOUNT_POINT" 2>/dev/null || true
        exit 1
    }

    log "Formatting $DATA_DEV with LUKS2 + Adiantum (camera parameters)..."
    echo -n "$DERIVED_KEY" | cryptsetup luksFormat --type luks2 \
        --cipher xchacha20,aes-adiantum-plain64 \
        --hash sha256 \
        --key-size 256 \
        --pbkdf argon2id \
        --pbkdf-memory 65536 \
        --pbkdf-force-iterations 4 \
        --pbkdf-parallel 1 \
        --batch-mode \
        --key-file=- \
        "$DATA_DEV"

    # Open the LUKS container
    echo -n "$DERIVED_KEY" | cryptsetup luksOpen "$DATA_DEV" "$DM_NAME" --key-file=-

    # Store keyfile for auto-unlock on subsequent boots
    install -d -m 0700 /etc/cryptsetup-keys.d
    echo -n "$DERIVED_KEY" > /etc/cryptsetup-keys.d/data.key
    chmod 0400 /etc/cryptsetup-keys.d/data.key

    # Clear key from memory
    DERIVED_KEY=""

    log "Camera LUKS formatting complete (keyfile stored for auto-unlock)"
else
    log "ERROR: Cannot determine device type (server or camera)"
    exit 1
fi

# --- Create filesystem inside LUKS container ---
log "Creating ext4 filesystem on /dev/mapper/$DM_NAME..."
mkfs.ext4 -L data /dev/mapper/"$DM_NAME"

# Mount
mount /dev/mapper/"$DM_NAME" "$MOUNT_POINT"
log "Mounted encrypted /data at $MOUNT_POINT"

# --- LUKS header backup (server only) ---
if is_server; then
    log "Backing up LUKS header to /boot/luks-header.bak..."
    cryptsetup luksHeaderBackup "$DATA_DEV" \
        --header-backup-file /boot/luks-header.bak
    chmod 0400 /boot/luks-header.bak
fi

log "=== LUKS first-boot encryption complete ==="

# Hand a clean LED state to the hotspot / streamer. The hotspot
# script will re-arm the LED to slow-blink if setup is still
# pending, or leave it solid once everything is up.
led_off
