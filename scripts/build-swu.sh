#!/usr/bin/env bash
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
SIGNING_KEY="$KEY_DIR/ota-signing.key"
SIGNING_CERT="$KEY_DIR/ota-signing.crt"

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

# Target partition is NOT baked into the bundle.
# Invariant: the STANDBY slot is the one we are NOT currently booted from.
# post-update.sh `preinst` reads live `boot_slot` and symlinks
# `/dev/monitor_standby` → correct standby partition; sw-description
# references that stable name. This keeps the bundle partition-agnostic
# and works regardless of which slot the device is currently on
# (previously hardcoded to p3, which no-op'd on devices already on B).

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
echo "    Target partition: resolved on device via /dev/monitor_standby"
echo "    Working dir: $WORK_DIR"

# 1. Copy payloads first so we can hash them before stamping
#    sw-description. SWUpdate built with CONFIG_SIGNED_IMAGES requires
#    every image and shellscript entry to declare a sha256 that it
#    verifies on the device against the actual bytes it receives.
#    Without it the daemon aborts with "Hash not set for rootfs.ext4.gz".
cp "$SWU_TEMPLATES/post-update.sh" "$WORK_DIR/post-update.sh"
chmod +x "$WORK_DIR/post-update.sh"
cp "$ROOTFS" "$WORK_DIR/rootfs.ext4.gz"

ROOTFS_SHA256=$(sha256sum "$WORK_DIR/rootfs.ext4.gz" | awk '{print $1}')
POST_UPDATE_SHA256=$(sha256sum "$WORK_DIR/post-update.sh" | awk '{print $1}')

# 2. Generate sw-description from template, substituting version + hashes
sed -e "s|@@VERSION@@|$VERSION|g" \
    -e "s|@@ROOTFS_SHA256@@|$ROOTFS_SHA256|g" \
    -e "s|@@POST_UPDATE_SHA256@@|$POST_UPDATE_SHA256|g" \
    "$SW_DESC_TEMPLATE" > "$WORK_DIR/sw-description"

echo ">>> sw-description:"
cat "$WORK_DIR/sw-description"

# 3. Sign sw-description if requested (CMS/PKCS7 for SWUpdate)
if [ "$SIGN" = true ]; then
    if [ ! -f "$SIGNING_KEY" ]; then
        echo ">>> Keys not found. Run './scripts/generate-ota-keys.sh' first."
        echo "    Expected: $SIGNING_KEY"
        exit 1
    fi

    # CMS signing is what SWUpdate verifies on-device. Use a certificate-based
    # keypair that OpenSSL CMS supports cleanly (ECDSA P-256 in this repo).
    openssl cms -sign -in "$WORK_DIR/sw-description" \
        -out "$WORK_DIR/sw-description.sig" \
        -signer "$SIGNING_CERT" -inkey "$SIGNING_KEY" \
        -outform DER -nosmimecap -binary -noattr
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

# --- Post-build sanity gate: fstab must carry /data + /boot ---
# Regression guard for the 2026-04-17 OTA-brick incident where
# do_image_ext4 produced a rootfs without /data in fstab.
# Mandatory — a missing/unreadable gate script is itself a failure,
# otherwise a bad checkout could silently re-introduce the regression.
VERIFY="$SCRIPT_DIR/verify-swu-fstab.sh"
if [ ! -f "$VERIFY" ]; then
    echo "ABORT: $VERIFY missing. Cannot verify bundle fstab."
    rm -f "$OUTPUT"
    exit 1
fi
chmod +x "$VERIFY" 2>/dev/null || true
echo ""
echo ">>> Verifying fstab inside bundle ..."
if ! "$VERIFY" "$OUTPUT"; then
    echo ""
    echo "ABORT: bundle failed fstab verification. Removing $OUTPUT."
    rm -f "$OUTPUT"
    exit 1
fi

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
