#!/bin/bash
# =============================================================
# build-swu.sh — Build SWUpdate (.swu) bundle for OTA updates
#
# Usage:
#   ./scripts/build-swu.sh --target <server|camera> --rootfs <path/to/rootfs.ext4.gz> [--version <ver>] [--sign]
#
# This script packages a compressed rootfs image into a .swu
# bundle that SWUpdate can install for an A/B partition swap.
#
# The .swu is a CPIO archive containing:
#   1. sw-description  (must be first entry — SWUpdate requirement)
#   2. sw-description.sig (CMS signature, if --sign is used)
#   3. post-update.sh  (sets U-Boot env for slot switch)
#   4. rootfs.ext4.gz  (compressed root filesystem)
#
# The target device determines:
#   - Hardware compatibility ID (hwrevision on device)
#   - Target partition for the raw write
#
# After applying: device reboots into new slot, swupdate-check.sh
# runs health checks, confirms or lets U-Boot rollback.
#
# Prerequisites:
#   - Yocto build must produce ext4.gz (IMAGE_FSTYPES += "ext4.gz")
#   - Device must have SWUpdate + U-Boot A/B configured (ADR-0008)
#
# Examples:
#   # Build unsigned camera update
#   ./scripts/build-swu.sh --target camera \
#       --rootfs build-zero2w/tmp-glibc/deploy/images/raspberrypi0-2w-64/home-camera-image-dev-raspberrypi0-2w-64.rootfs.ext4.gz
#
#   # Build signed server update
#   ./scripts/build-swu.sh --target server \
#       --rootfs build/tmp-glibc/deploy/images/raspberrypi4-64/home-monitor-image-dev-raspberrypi4-64.rootfs.ext4.gz \
#       --sign
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SWU_TEMPLATES="$REPO_DIR/swupdate"
KEY_DIR="${KEY_DIR:-$HOME/.monitor-keys}"

# --- Parse arguments ---
TARGET=""
ROOTFS=""
VERSION=""
SIGN=false

usage() {
    echo "Usage: $0 --target <server|camera> --rootfs <path> [--version <ver>] [--sign]"
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target)  TARGET="$2"; shift 2 ;;
        --rootfs)  ROOTFS="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --sign)    SIGN=true; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [ -z "$TARGET" ] || [ -z "$ROOTFS" ]; then
    usage
fi

if [ ! -f "$ROOTFS" ]; then
    echo "Error: rootfs not found: $ROOTFS"
    exit 1
fi

# --- Determine target-specific values ---
case "$TARGET" in
    server)
        SW_DESC_TEMPLATE="$SWU_TEMPLATES/sw-description.server"
        ;;
    camera)
        SW_DESC_TEMPLATE="$SWU_TEMPLATES/sw-description.camera"
        ;;
    *)
        echo "Error: target must be 'server' or 'camera'"
        exit 1
        ;;
esac

# Detect current slot on device to determine target partition
# Default: write to slot B (partition 3) since devices ship on slot A
TARGET_PART="/dev/mmcblk0p3"

# Auto-detect version from rootfs path if not specified
if [ -z "$VERSION" ]; then
    # Try to extract from rootfs filename timestamp or use date
    VERSION="1.1.0-$(date +%Y%m%d)"
fi

# --- Build in temp directory ---
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

echo ">>> Building SWU bundle for $TARGET (version: $VERSION)"
echo "    Rootfs: $ROOTFS"
echo "    Target partition: $TARGET_PART"
echo "    Working dir: $WORK_DIR"

# 1. Generate sw-description from template
sed -e "s|@@VERSION@@|$VERSION|g" \
    -e "s|@@TARGET_PART@@|$TARGET_PART|g" \
    "$SW_DESC_TEMPLATE" > "$WORK_DIR/sw-description"

echo ">>> sw-description:"
cat "$WORK_DIR/sw-description"

# 2. Copy post-update script
cp "$SWU_TEMPLATES/post-update.sh" "$WORK_DIR/post-update.sh"
chmod +x "$WORK_DIR/post-update.sh"

# 3. Copy rootfs (renamed to match sw-description)
cp "$ROOTFS" "$WORK_DIR/rootfs.ext4.gz"

# 4. Sign sw-description if requested (CMS/PKCS7 for SWUpdate)
if [ "$SIGN" = true ]; then
    SIGNING_KEY="$KEY_DIR/ota-signing.key"
    SIGNING_CERT="$KEY_DIR/ota-signing.crt"

    if [ ! -f "$SIGNING_KEY" ]; then
        echo ">>> Generating OTA signing keypair + self-signed cert..."
        mkdir -p "$KEY_DIR"
        # Generate Ed25519 private key
        openssl genpkey -algorithm Ed25519 -out "$SIGNING_KEY"
        # Generate self-signed certificate (needed for CMS)
        openssl req -new -x509 -key "$SIGNING_KEY" -out "$SIGNING_CERT" \
            -days 3650 -subj "/CN=Home Monitor OTA Signing"
        # Export public key for device
        openssl x509 -in "$SIGNING_CERT" -pubkey -noout > "$KEY_DIR/ota-signing.pub"
        chmod 600 "$SIGNING_KEY"
        echo "    Private key: $SIGNING_KEY"
        echo "    Certificate: $SIGNING_CERT"
        echo "    Public key:  $KEY_DIR/ota-signing.pub"
        echo ""
        echo "    IMPORTANT: Deploy ota-signing.crt to /etc/swupdate-public.pem on devices"
    fi

    # CMS sign sw-description
    openssl cms -sign -in "$WORK_DIR/sw-description" \
        -out "$WORK_DIR/sw-description.sig" \
        -signer "$SIGNING_CERT" -inkey "$SIGNING_KEY" \
        -outform DER -noattr -binary
    echo ">>> sw-description signed (CMS/PKCS7)"
fi

# 5. Package as CPIO archive (.swu)
#    sw-description MUST be the first entry (SWUpdate requirement)
OUTPUT="$REPO_DIR/${TARGET}-update-${VERSION}.swu"

cd "$WORK_DIR"

FILES="sw-description"
if [ "$SIGN" = true ]; then
    FILES="$FILES\nsw-description.sig"
fi
FILES="$FILES\npost-update.sh\nrootfs.ext4.gz"

printf "%b\n" "$FILES" | cpio -o -H crc > "$OUTPUT" 2>/dev/null

# --- Summary ---
SWU_SIZE=$(ls -lh "$OUTPUT" | awk '{print $5}')
echo ""
echo ">>> SWU bundle created: $OUTPUT ($SWU_SIZE)"
echo ""
echo "To apply on $TARGET:"
echo "  1. Copy to device:  scp $OUTPUT root@<${TARGET}-ip>:/data/ota/"
echo "  2. Install:         ssh root@<${TARGET}-ip> swupdate -i /data/ota/$(basename "$OUTPUT")"
echo "  3. Reboot:          ssh root@<${TARGET}-ip> reboot"
echo "  4. Verify:          ssh root@<${TARGET}-ip> fw_printenv boot_slot upgrade_available"
