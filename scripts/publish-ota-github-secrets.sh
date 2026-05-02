#!/usr/bin/env bash
# REQ: SWR-047, SWR-034; RISK: RISK-019; SEC: SC-017, SC-018; TEST: TC-032, TC-043, TC-045
# =============================================================
# publish-ota-github-secrets.sh — Upload OTA signing materials to GitHub secrets
#
# Usage:
#   ./scripts/publish-ota-github-secrets.sh [--repo owner/name] [--recovery-passphrase-file <file>]
# =============================================================
set -euo pipefail

KEY_DIR="${KEY_DIR:-$HOME/.monitor-keys}"
REPO=""
RECOVERY_PASSPHRASE_FILE=""

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
    echo "Usage: ./scripts/publish-ota-github-secrets.sh [--repo owner/name] [--recovery-passphrase-file <file>]" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --repo)
            REPO="$2"
            shift 2
            ;;
        --recovery-passphrase-file)
            RECOVERY_PASSPHRASE_FILE="$2"
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

if [ -n "$RECOVERY_PASSPHRASE_FILE" ]; then
    RECOVERY_PASSPHRASE_FILE="$(resolve_path "$RECOVERY_PASSPHRASE_FILE")"
    if [ ! -f "$RECOVERY_PASSPHRASE_FILE" ]; then
        echo "ERROR: recovery passphrase file not found: $RECOVERY_PASSPHRASE_FILE" >&2
        exit 1
    fi
fi

gh secret set OTA_SIGNING_KEY --repo "$REPO" < "$SIGNING_KEY"
gh secret set OTA_SIGNING_CERT --repo "$REPO" < "$SIGNING_CERT"

echo "Uploaded GitHub Actions secrets for $REPO:"
echo "  OTA_SIGNING_KEY"
echo "  OTA_SIGNING_CERT"
echo "This is optional maintainer automation, not the default self-hosted user flow."

if [ -n "$RECOVERY_PASSPHRASE_FILE" ]; then
    gh secret set OTA_BACKUP_RECOVERY_PASSPHRASE --repo "$REPO" < "$RECOVERY_PASSPHRASE_FILE"
    echo "  OTA_BACKUP_RECOVERY_PASSPHRASE"
fi
