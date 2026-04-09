#!/bin/bash
# =============================================================
# sign-image.sh — Sign OTA images with Ed25519
#
# Usage:
#   ./scripts/sign-image.sh <image.swu>
#
# On first run, generates a signing keypair in ~/.monitor-keys/
# The public key must be embedded in the device rootfs.
# =============================================================
set -e

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
    openssl genpkey -algorithm Ed25519 -out "$PRIVATE_KEY"
    openssl pkey -in "$PRIVATE_KEY" -pubout -out "$PUBLIC_KEY"
    chmod 600 "$PRIVATE_KEY"
    echo ">>> Keys generated:"
    echo "    Private: $PRIVATE_KEY (KEEP SECRET)"
    echo "    Public:  $PUBLIC_KEY (embed in device rootfs)"
fi

# Sign the image
SIGNATURE="${IMAGE}.sig"
openssl pkeyutl -sign -inkey "$PRIVATE_KEY" -rawin -in "$IMAGE" -out "$SIGNATURE"

echo ">>> Signed: $SIGNATURE"
echo ">>> Verify with: openssl pkeyutl -verify -pubin -inkey $PUBLIC_KEY -rawin -in $IMAGE -sigfile $SIGNATURE"
