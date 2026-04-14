#!/usr/bin/env bash
# =============================================================
# publish-ota-github-secrets.sh — Upload OTA signing materials to GitHub secrets
#
# Usage:
#   ./scripts/publish-ota-github-secrets.sh [--repo owner/name]
# =============================================================
set -euo pipefail

KEY_DIR="${KEY_DIR:-$HOME/.monitor-keys}"
REPO=""

usage() {
    echo "Usage: ./scripts/publish-ota-github-secrets.sh [--repo owner/name]" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)
            REPO="$2"
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

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: gh CLI is required" >&2
    exit 1
fi

if [ -z "$REPO" ]; then
    REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
fi

gh secret set OTA_SIGNING_KEY --repo "$REPO" < "$SIGNING_KEY"
gh secret set OTA_SIGNING_CERT --repo "$REPO" < "$SIGNING_CERT"

echo "Uploaded GitHub Actions secrets for $REPO:"
echo "  OTA_SIGNING_KEY"
echo "  OTA_SIGNING_CERT"
