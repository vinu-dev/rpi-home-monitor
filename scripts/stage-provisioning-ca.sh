#!/usr/bin/env bash
# REQ: SWR-001, SWR-034, SWR-046; RISK: RISK-002, RISK-019; SEC: SC-001, SC-017, SC-018; TEST: TC-032, TC-043
# Stage the Home Monitor Provisioning CA public certificate for Yocto.
#
# This script does not generate CA material. The CA generator is a separate
# private project. Copy/export only the public CA certificate into
# local-secrets/provisioning-ca/ before running Yocto builds. BitBake
# parses recipes globally, so camera-only builds also need this public
# file staged even though the camera image does not package it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_CERT="$REPO_ROOT/local-secrets/provisioning-ca/home-monitor-provisioning-ca.crt"
GENERATED_CERT="$REPO_ROOT/app/server/config/generated/trust/home-monitor-provisioning-ca.crt"

if [ ! -f "$SOURCE_CERT" ]; then
    cat >&2 <<EOF
ERROR: missing Home Monitor Provisioning CA public certificate.

Expected:
  $SOURCE_CERT

Create/export it from the separate private CA generator project, then rerun:
  python home_monitor_ca.py export-public-ca --dest "$SOURCE_CERT"

Only the public CA certificate belongs here. Do not copy CA private keys,
client .p12 bundles, or passphrase files into the RPI repo workspace.
EOF
    exit 1
fi

mkdir -p "$(dirname "$GENERATED_CERT")"
cp "$SOURCE_CERT" "$GENERATED_CERT"
chmod 0644 "$GENERATED_CERT"

echo ">>> Staged Home Monitor Provisioning CA public cert:"
echo "    $GENERATED_CERT"
