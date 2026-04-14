#!/usr/bin/env bash
# =============================================================
# backup-ota-keys.sh — Encrypt and package the active OTA signing keypair
#
# Usage:
#   ./scripts/backup-ota-keys.sh [--output <file>] \
#       [--passphrase-file <file>] [--generate-passphrase-file <file>]
#
# The backup contains:
#   - ota-signing.key
#   - ota-signing.crt
#   - metadata.txt (fingerprints + timestamps)
#
# The archive is encrypted with AES-256-CBC + PBKDF2 and is safe to store in
# a private repo, cloud storage, or offline media. The passphrase must be
# stored separately.
# =============================================================
set -euo pipefail

KEY_DIR="${KEY_DIR:-$HOME/.monitor-keys}"
BACKUP_DIR="${BACKUP_DIR:-$KEY_DIR/backups}"
OUTPUT=""
PASSPHRASE_FILE=""
GENERATE_PASSPHRASE_FILE=""
DATE_STAMP="$(date +%Y%m%d-%H%M%S)"

resolve_path() {
    case "$1" in
        "~")
            printf '%s\n' "$HOME"
            ;;
        "~/"*)
            printf '%s/%s\n' "$HOME" "${1#~/}"
            ;;
        *)
            printf '%s\n' "$1"
            ;;
    esac
}

usage() {
    cat <<'EOF'
Usage: ./scripts/backup-ota-keys.sh [--output <file>] [--passphrase-file <file>] [--generate-passphrase-file <file>]

Options:
  --output <file>                    Output encrypted backup path
  --passphrase-file <file>           Read encryption passphrase from file
  --generate-passphrase-file <file>  Generate a new recovery passphrase and write it there

Environment:
  OTA_BACKUP_PASSPHRASE              Passphrase alternative to --passphrase-file
  KEY_DIR                            Defaults to ~/.monitor-keys
  BACKUP_DIR                         Defaults to ~/.monitor-keys/backups
EOF
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --passphrase-file)
            PASSPHRASE_FILE="$2"
            shift 2
            ;;
        --generate-passphrase-file)
            GENERATE_PASSPHRASE_FILE="$2"
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

SIGNING_KEY="$KEY_DIR/ota-signing.key"
SIGNING_CERT="$KEY_DIR/ota-signing.crt"

if [ ! -f "$SIGNING_KEY" ] || [ ! -f "$SIGNING_CERT" ]; then
    echo "ERROR: OTA signing keypair not found in $KEY_DIR" >&2
    exit 1
fi

if [ -n "$GENERATE_PASSPHRASE_FILE" ]; then
    GENERATE_PASSPHRASE_FILE="$(resolve_path "$GENERATE_PASSPHRASE_FILE")"
    mkdir -p "$(dirname "$GENERATE_PASSPHRASE_FILE")"
    umask 077
    openssl rand -base64 48 > "$GENERATE_PASSPHRASE_FILE"
    chmod 600 "$GENERATE_PASSPHRASE_FILE"
    PASSPHRASE_FILE="$GENERATE_PASSPHRASE_FILE"
fi

if [ -n "$PASSPHRASE_FILE" ]; then
    PASSPHRASE_FILE="$(resolve_path "$PASSPHRASE_FILE")"
fi

if [ -z "${OTA_BACKUP_PASSPHRASE:-}" ] && [ -z "$PASSPHRASE_FILE" ]; then
    echo "ERROR: provide OTA_BACKUP_PASSPHRASE or --passphrase-file (or --generate-passphrase-file)" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
if [ -z "$OUTPUT" ]; then
    OUTPUT="$BACKUP_DIR/ota-signing-backup-$DATE_STAMP.tar.gz.enc"
fi
OUTPUT="$(resolve_path "$OUTPUT")"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

cp "$SIGNING_KEY" "$WORK_DIR/ota-signing.key"
cp "$SIGNING_CERT" "$WORK_DIR/ota-signing.crt"

CERT_FINGERPRINT="$(openssl x509 -in "$SIGNING_CERT" -sha256 -fingerprint -noout | cut -d= -f2)"
KEY_FINGERPRINT="$(openssl pkey -in "$SIGNING_KEY" -pubout -outform DER 2>/dev/null | openssl dgst -sha256 | awk '{print $2}')"

cat > "$WORK_DIR/metadata.txt" <<EOF
created_at_utc=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
signing_cert_sha256_fingerprint=$CERT_FINGERPRINT
signing_public_key_sha256=$KEY_FINGERPRINT
repo_public_cert_path=meta-home-monitor/recipes-support/swupdate/files/swupdate-public.crt
notes=Store the encrypted archive and the passphrase separately.
EOF

PLAIN_ARCHIVE="$WORK_DIR/ota-signing-backup.tar.gz"
(cd "$WORK_DIR" && tar -cf - ota-signing.key ota-signing.crt metadata.txt | gzip -c > "$PLAIN_ARCHIVE")

if [ -n "${OTA_BACKUP_PASSPHRASE:-}" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
        -in "$PLAIN_ARCHIVE" -out "$OUTPUT" \
        -pass env:OTA_BACKUP_PASSPHRASE
else
    openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
        -in "$PLAIN_ARCHIVE" -out "$OUTPUT" \
        -pass "file:$PASSPHRASE_FILE"
fi

sha256sum "$OUTPUT" > "${OUTPUT}.sha256"

echo "Encrypted OTA key backup written to:"
echo "  $OUTPUT"
echo "SHA-256 manifest:"
echo "  ${OUTPUT}.sha256"
if [ -n "$GENERATE_PASSPHRASE_FILE" ]; then
    echo "Recovery passphrase generated at:"
    echo "  $GENERATE_PASSPHRASE_FILE"
fi
