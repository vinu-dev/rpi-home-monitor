#!/usr/bin/env bash
# =============================================================
# build.sh — Clone layers, configure, and build Yocto images
#
# Usage:
#   ./scripts/build.sh server-dev     — RPi 4B development image
#   ./scripts/build.sh server-prod    — RPi 4B production image
#   ./scripts/build.sh camera-dev     — RPi Zero 2W development image
#   ./scripts/build.sh camera-prod    — RPi Zero 2W production image
#   ./scripts/build.sh all-dev        — both boards, development
#   ./scripts/build.sh all-prod       — both boards, production
#
# Legacy (builds dev by default):
#   ./scripts/build.sh server
#   ./scripts/build.sh camera
#   ./scripts/build.sh all
# =============================================================
set -euo pipefail

YOCTO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE="scarthgap"
NCPU=$(nproc)
TARGET="${1:-server-dev}"

echo ">>> Working in: $YOCTO_DIR"
echo ">>> Release: $RELEASE"
echo ">>> CPUs: $NCPU"
echo ">>> Target: $TARGET"

# --- OTA signing cert guard (prod only) ---
# Signing is disabled by default (SWUPDATE_SIGNING=0 in local.conf, see ADR-0014).
# For production builds with SWUPDATE_SIGNING=1, the cert must be present first:
#   ./scripts/generate-ota-keys.sh
OTA_CERT="$YOCTO_DIR/meta-home-monitor/recipes-support/swupdate/files/swupdate-public.crt"
if grep -q 'SWUPDATE_SIGNING.*=.*"1"' "$YOCTO_DIR/config/"*"/local.conf" 2>/dev/null; then
    if [ ! -f "$OTA_CERT" ]; then
        echo ""
        echo "ERROR: SWUPDATE_SIGNING=1 but OTA cert not found: $OTA_CERT"
        echo "Run './scripts/generate-ota-keys.sh' first to generate the keypair."
        echo ""
        exit 1
    fi
fi

# --- Clone Yocto layers ---
clone_layer() {
    local url=$1 dir=$2 branch=$3
    if [ ! -d "$dir/.git" ]; then
        echo ">>> Cloning $dir ..."
        git clone "$url" "$dir"
    fi
    cd "$dir"
    git checkout "$branch" 2>/dev/null || git checkout -b "$branch" "origin/$branch"
    cd "$YOCTO_DIR"
}

clone_layer "https://git.yoctoproject.org/poky" "$YOCTO_DIR/poky" "$RELEASE"
clone_layer "https://git.yoctoproject.org/meta-raspberrypi" "$YOCTO_DIR/meta-raspberrypi" "$RELEASE"
clone_layer "https://github.com/openembedded/meta-openembedded.git" "$YOCTO_DIR/meta-openembedded" "$RELEASE"
clone_layer "https://github.com/sbabic/meta-swupdate.git" "$YOCTO_DIR/meta-swupdate" "$RELEASE"

build_image() {
    local board=$1 builddir=$2 configdir=$3 image=$4

    echo ""
    echo "============================================"
    echo " Building: $image"
    echo " Board: $board"
    echo " Build dir: $builddir"
    echo " Cores: $NCPU"
    echo "============================================"
    echo ""

    # oe-init-build-env is not nounset-safe when BBSERVER is absent.
    set +u
    source "$YOCTO_DIR/poky/oe-init-build-env" "$builddir"
    set -u

    cp "$YOCTO_DIR/config/$configdir/local.conf" "$builddir/conf/local.conf"
    cp "$YOCTO_DIR/config/bblayers.conf" "$builddir/conf/bblayers.conf"

    sed -i "s/^BB_NUMBER_THREADS.*/BB_NUMBER_THREADS = \"$NCPU\"/" "$builddir/conf/local.conf"
    sed -i "s/^PARALLEL_MAKE.*/PARALLEL_MAKE = \"-j $NCPU\"/" "$builddir/conf/local.conf"

    bitbake "$image"
}

case "$TARGET" in
    server-dev|server)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image-dev"
        ;;
    server-prod)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image-prod"
        ;;
    camera-dev|camera)
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image-dev"
        ;;
    camera-prod)
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image-prod"
        ;;
    all-dev|all)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image-dev"
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image-dev"
        ;;
    all-prod)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image-prod"
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image-prod"
        ;;
    *)
        echo "Usage: $0 {server-dev|server-prod|camera-dev|camera-prod|all-dev|all-prod}"
        echo ""
        echo "  server-dev   RPi 4B development (debug-tweaks, root SSH)"
        echo "  server-prod  RPi 4B production (hardened, no root)"
        echo "  camera-dev   Zero 2W development"
        echo "  camera-prod  Zero 2W production"
        echo "  all-dev      Both boards, development"
        echo "  all-prod     Both boards, production"
        echo ""
        echo "Legacy aliases: server=server-dev, camera=camera-dev, all=all-dev"
        exit 1
        ;;
esac

echo ""
echo ">>> Build complete!"
