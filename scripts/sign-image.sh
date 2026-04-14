#!/usr/bin/env bash
# =============================================================
# sign-image.sh — Legacy detached signer for OTA artifacts
#
# Usage:
#   ./scripts/sign-image.sh <image.swu>
#
# On first run, generates an ECDSA P-256 signing keypair in ~/.monitor-keys/.
# This script produces a detached signature. For full-system SWUpdate bundles,
# prefer `./scripts/build-swu.sh --sign`, which generates the CMS signature that
# SWUpdate actually verifies on-device.
# =============================================================
set -euo pipefail

KEY_DIR="${HOME}/.monitor-keys"
PRIVATE_KEY="$KEY_DIR/ota-signing.key"
PUBLIC_KEY="$KEY_DIR/ota-signing.pub"

if [ -z "$1" ]; then
    echo "Usage: $0 <image.swu>"
    exit 1
fi

IMAGE="$1"

if [ ! -f "$IMAGE" ]; then
    echo "Error: $IMAGE not found"
    exit 1
fi

# Generate keys if they don't exist
if [ ! -f "$PRIVATE_KEY" ]; then
    echo ">>> Generating OTA signing keypair..."
    mkdir -p "$KEY_DIR"
    openssl ecparam -name prime256v1 -genkey -noout -out "$PRIVATE_KEY"
    openssl pkey -in "$PRIVATE_KEY" -pubout -out "$PUBLIC_KEY"
    chmod 600 "$PRIVATE_KEY"
    echo ">>> Keys generated:"
    echo "    Private: $PRIVATE_KEY (KEEP SECRET)"
    echo "    Public:  $PUBLIC_KEY (embed in device rootfs)"
fi

# Sign the image with a detached SHA-256 / ECDSA signature
SIGNATURE="${IMAGE}.sig"
openssl dgst -sha256 -sign "$PRIVATE_KEY" -out "$SIGNATURE" "$IMAGE"

echo ">>> Signed: $SIGNATURE"
echo ">>> Verify with: openssl dgst -sha256 -verify $PUBLIC_KEY -signature $SIGNATURE $IMAGE"
