#!/usr/bin/env bash
# REQ: SWR-047, SWR-034; RISK: RISK-019; SEC: SC-017, SC-018; TEST: TC-032, TC-043, TC-045
# generate-sbom.sh — Generate Software Bill of Materials (SBOM)
#
# Produces CycloneDX JSON SBOMs for:
#   1. Server Python application (pip dependencies)
#   2. Camera Python application (pip dependencies)
#   3. Yocto OS image (from SPDX build output, if available)
#
# Usage:
#   ./scripts/generate-sbom.sh                    # Generate app SBOMs only
#   ./scripts/generate-sbom.sh --yocto <deploy>   # Include Yocto SPDX conversion
#
# Output: sbom/ directory with versioned CycloneDX JSON files
#
# Industry standards: CycloneDX 1.5 (OWASP), compatible with:
#   - NTIA minimum elements for SBOM
#   - EU Cyber Resilience Act (CRA)
#   - FDA premarket cybersecurity guidance

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
SBOM_DIR="$REPO_DIR/sbom"
VERSION=$(git -C "$REPO_DIR" describe --tags --always 2>/dev/null || echo "dev")
DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

mkdir -p "$SBOM_DIR"

echo "=== Generating SBOMs (version: $VERSION) ==="

# --- 1. Server app SBOM ---
echo "--- Server application SBOM ---"
cat > "$SBOM_DIR/server-app.cdx.json" << EOF
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.5",
  "serialNumber": "urn:uuid:$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')",
  "version": 1,
  "metadata": {
    "timestamp": "$DATE",
    "component": {
      "type": "application",
      "name": "monitor-server",
      "version": "$VERSION",
      "description": "RPi Home Monitor server application",
      "licenses": [{"license": {"id": "MIT"}}]
    },
    "tools": [{"name": "generate-sbom.sh", "version": "1.0"}]
  },
  "components": [
    {
      "type": "framework",
      "name": "flask",
      "version": ">=3.0",
      "purl": "pkg:pypi/flask",
      "scope": "required"
    },
    {
      "type": "library",
      "name": "bcrypt",
      "version": ">=4.0",
      "purl": "pkg:pypi/bcrypt",
      "scope": "required"
    },
    {
      "type": "library",
      "name": "jinja2",
      "version": ">=3.0",
      "purl": "pkg:pypi/jinja2",
      "scope": "required"
    }
  ]
}
EOF
echo "  Created: sbom/server-app.cdx.json"

# --- 2. Camera app SBOM ---
echo "--- Camera application SBOM ---"
cat > "$SBOM_DIR/camera-app.cdx.json" << EOF
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.5",
  "serialNumber": "urn:uuid:$(cat /proc/sys/kernel/random/uuid 2>/dev/null || python3 -c 'import uuid; print(uuid.uuid4())')",
  "version": 1,
  "metadata": {
    "timestamp": "$DATE",
    "component": {
      "type": "application",
      "name": "camera-streamer",
      "version": "$VERSION",
      "description": "RPi Home Monitor camera streaming service",
      "licenses": [{"license": {"id": "MIT"}}]
    },
    "tools": [{"name": "generate-sbom.sh", "version": "1.0"}]
  },
  "components": []
}
EOF
echo "  Created: sbom/camera-app.cdx.json (no pip dependencies — stdlib only)"

# --- 3. Yocto OS manifest (package list from image) ---
# This is extracted from the Yocto build manifest file
# (tmp-glibc/deploy/images/<machine>/<image>.manifest)
if [[ "${1:-}" == "--yocto" ]] && [[ -n "${2:-}" ]]; then
    DEPLOY_DIR="$2"
    echo "--- Yocto OS image SBOM ---"

    for manifest in "$DEPLOY_DIR"/*.manifest; do
        [ -f "$manifest" ] || continue
        IMAGE_NAME=$(basename "$manifest" .manifest)
        OUT="$SBOM_DIR/${IMAGE_NAME}-os.cdx.json"

        # Parse Yocto manifest (format: package arch version)
        COMPONENTS=""
        while IFS=' ' read -r pkg arch ver; do
            [ -z "$pkg" ] && continue
            if [ -n "$COMPONENTS" ]; then
                COMPONENTS="$COMPONENTS,"
            fi
            COMPONENTS="$COMPONENTS
    {\"type\":\"library\",\"name\":\"$pkg\",\"version\":\"$ver\",\"purl\":\"pkg:yocto/$pkg@$ver?arch=$arch\"}"
        done < "$manifest"

        cat > "$OUT" << EOFYOCTO
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.5",
  "serialNumber": "urn:uuid:$(python3 -c 'import uuid; print(uuid.uuid4())')",
  "version": 1,
  "metadata": {
    "timestamp": "$DATE",
    "component": {
      "type": "operating-system",
      "name": "$IMAGE_NAME",
      "version": "$VERSION",
      "description": "Home Monitor OS Yocto image"
    }
  },
  "components": [$COMPONENTS
  ]
}
EOFYOCTO
        echo "  Created: sbom/${IMAGE_NAME}-os.cdx.json"
    done
else
    echo "--- Skipping Yocto OS SBOM (use --yocto <deploy-dir> to include) ---"
fi

echo ""
echo "=== SBOMs generated in sbom/ ==="
ls -la "$SBOM_DIR"/*.cdx.json 2>/dev/null
