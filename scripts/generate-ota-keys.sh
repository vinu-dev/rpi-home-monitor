#!/usr/bin/env bash
# =============================================================
# generate-ota-keys.sh — Generate OTA signing keypair
#
# Usage:
#   ./scripts/generate-ota-keys.sh
#
# Generates an ECDSA P-256 keypair for signing OTA (.swu) bundles.
# The private key is stored locally in ~/.monitor-keys/ (never
# in the repo). The public certificate is copied into the Yocto
# recipe so it gets baked into device images at build time.
#
# Run this ONCE before your first build. Re-run only to rotate keys.
#
# Key files:
#   ~/.monitor-keys/ota-signing.key  — private key  (KEEP SECRET)
#   ~/.monitor-keys/ota-signing.crt  — certificate
#   meta-home-monitor/recipes-support/swupdate/files/swupdate-public.crt
#                                    — public cert baked into device images
# =============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
KEY_DIR="${HOME}/.monitor-keys"
CERT_DEST="$REPO_DIR/meta-home-monitor/recipes-support/swupdate/files/swupdate-public.crt"

echo ">>> Home Monitor OTA Key Generation"
echo ""

if [ -f "$KEY_DIR/ota-signing.key" ] && [ -f "$KEY_DIR/ota-signing.crt" ]; then
    echo ">>> Keys already exist at $KEY_DIR/ — skipping generation."
    echo "    To rotate keys, delete $KEY_DIR/ota-signing.key and re-run."
else
    echo ">>> Generating ECDSA P-256 OTA signing keypair..."
    mkdir -p "$KEY_DIR"
    chmod 700 "$KEY_DIR"

    # Generate ECDSA P-256 private key for CMS/PKCS7 bundle signing.
    openssl ecparam -name prime256v1 -genkey -noout -out "$KEY_DIR/ota-signing.key"
    chmod 600 "$KEY_DIR/ota-signing.key"

    # Generate self-signed certificate (CMS/PKCS7 requires a cert, not raw pubkey)
    # MSYS_NO_PATHCONV=1 prevents Git Bash from mangling the -subj argument on Windows
    MSYS_NO_PATHCONV=1 openssl req -new -x509 \
        -key "$(cygpath -w "$KEY_DIR/ota-signing.key" 2>/dev/null || echo "$KEY_DIR/ota-signing.key")" \
        -out "$(cygpath -w "$KEY_DIR/ota-signing.crt" 2>/dev/null || echo "$KEY_DIR/ota-signing.crt")" \
        -days 3650 \
        -subj "/CN=Home Monitor OTA Signing"

    echo ">>> Generated:"
    echo "    Private key: $KEY_DIR/ota-signing.key  (never commit this)"
    echo "    Certificate: $KEY_DIR/ota-signing.crt"
fi

echo ""
echo ">>> Copying public cert into Yocto recipe..."
cp "$KEY_DIR/ota-signing.crt" "$CERT_DEST"
echo "    -> $CERT_DEST"

echo ""
echo ">>> Done."
echo ""
echo "Next steps:"
echo "  1. Rebuild:     ./scripts/build.sh camera-dev   (or server-dev)"
echo "  2. Sign bundles: ./scripts/build-swu.sh --target camera --rootfs <path> --sign"
echo ""
echo "The cert is baked into every image built from this repo."
echo "Sign all .swu bundles with: $KEY_DIR/ota-signing.key"
