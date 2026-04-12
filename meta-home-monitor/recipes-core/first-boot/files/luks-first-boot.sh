#!/bin/sh
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
# =============================================================
set -e

STAMP="/data/.luks-done"
DATA_DEV="/dev/mmcblk0p4"
DM_NAME="data"
MOUNT_POINT="/data"

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

    # Server uses passphrase with 1 GB argon2id memory
    PASSPHRASE=""
    while [ -z "$PASSPHRASE" ]; do
        PASSPHRASE=$(systemd-ask-password --timeout=0 \
            "Create encryption passphrase for /data (min 12 chars):")

        # Check minimum length
        if [ "${#PASSPHRASE}" -lt 12 ]; then
            log "Passphrase too short (minimum 12 characters)"
            PASSPHRASE=""
            continue
        fi

        # Confirm
        CONFIRM=$(systemd-ask-password --timeout=0 \
            "Confirm encryption passphrase:")
        if [ "$PASSPHRASE" != "$CONFIRM" ]; then
            log "Passphrases do not match"
            PASSPHRASE=""
        fi
    done

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
        log "ERROR: No pairing_secret found — cannot derive LUKS key"
        log "Camera must be paired with server before LUKS encryption"
        log "Skipping LUKS formatting — /data will remain unencrypted"
        # Mount as plain partition so camera can function
        mount "$DATA_DEV" "$MOUNT_POINT" 2>/dev/null || true
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
