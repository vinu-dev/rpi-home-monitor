#!/usr/bin/env bash
# =============================================================
# restore-ota-keys.sh — Decrypt and restore an OTA signing key backup
#
# Usage:
#   ./scripts/restore-ota-keys.sh --input <backup.tar.gz.enc> \
#       [--key-dir <dir>] [--passphrase-file <file>]
# =============================================================
set -euo pipefail

KEY_DIR="${KEY_DIR:-$HOME/.monitor-keys}"
INPUT=""
PASSPHRASE_FILE=""

resolve_path() {
    case "$1" in
        \~)
            printf '%s\n' "$HOME"
            ;;
        \~/*)
            printf '%s/%s\n' "$HOME" "${1#~/}"
            ;;
        *)
            printf '%s\n' "$1"
            ;;
    esac
}

usage() {
    cat <<'EOF'
Usage: ./scripts/restore-ota-keys.sh --input <backup.tar.gz.enc> [--key-dir <dir>] [--passphrase-file <file>]

Environment:
  OTA_BACKUP_PASSPHRASE   Passphrase alternative to --passphrase-file
  KEY_DIR                 Defaults to ~/.monitor-keys
EOF
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --input)
            INPUT="$2"
            shift 2
            ;;
        --key-dir)
            KEY_DIR="$2"
            shift 2
            ;;
        --passphrase-file)
            PASSPHRASE_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            ;;
    esac
done

if [ -z "$INPUT" ] || [ ! -f "$INPUT" ]; then
    echo "ERROR: backup input file not found" >&2
    exit 1
fi
INPUT="$(resolve_path "$INPUT")"
KEY_DIR="$(resolve_path "$KEY_DIR")"
if [ -n "$PASSPHRASE_FILE" ]; then
    PASSPHRASE_FILE="$(resolve_path "$PASSPHRASE_FILE")"
fi

if [ ! -f "$INPUT" ]; then
    echo "ERROR: backup input file not found" >&2
    exit 1
fi

if [ -z "${OTA_BACKUP_PASSPHRASE:-}" ] && [ -z "$PASSPHRASE_FILE" ]; then
    echo "ERROR: provide OTA_BACKUP_PASSPHRASE or --passphrase-file" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
PLAIN_ARCHIVE="$WORK_DIR/ota-signing-backup.tar.gz"

if [ -n "${OTA_BACKUP_PASSPHRASE:-}" ]; then
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
        -in "$INPUT" -out "$PLAIN_ARCHIVE" \
        -pass env:OTA_BACKUP_PASSPHRASE
else
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
        -in "$INPUT" -out "$PLAIN_ARCHIVE" \
        -pass "file:$PASSPHRASE_FILE"
fi

mkdir -p "$KEY_DIR"
tar -C "$KEY_DIR" -xzf "$PLAIN_ARCHIVE"
chmod 600 "$KEY_DIR/ota-signing.key" "$KEY_DIR/ota-signing.crt" 2>/dev/null || true

echo "OTA signing keypair restored into:"
echo "  $KEY_DIR"
echo "Metadata:"
echo "  $KEY_DIR/metadata.txt"
