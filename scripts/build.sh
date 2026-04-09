#!/bin/bash
# =============================================================
# build.sh — Clone layers, configure, and build Yocto images
# Usage:
#   ./scripts/build.sh server    — build RPi 4B server image
#   ./scripts/build.sh camera    — build RPi Zero 2W camera image
#   ./scripts/build.sh all       — build both
# =============================================================
set -e

YOCTO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RELEASE="scarthgap"
NCPU=$(nproc)
TARGET="${1:-server}"

echo ">>> Working in: $YOCTO_DIR"
echo ">>> Release: $RELEASE"
echo ">>> CPUs: $NCPU"
echo ">>> Target: $TARGET"

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

    source "$YOCTO_DIR/poky/oe-init-build-env" "$builddir"

    cp "$YOCTO_DIR/config/$configdir/local.conf" "$builddir/conf/local.conf"
    cp "$YOCTO_DIR/config/bblayers.conf" "$builddir/conf/bblayers.conf"

    sed -i "s/^BB_NUMBER_THREADS.*/BB_NUMBER_THREADS = \"$NCPU\"/" "$builddir/conf/local.conf"
    sed -i "s/^PARALLEL_MAKE.*/PARALLEL_MAKE = \"-j $NCPU\"/" "$builddir/conf/local.conf"

    bitbake "$image"
}

case "$TARGET" in
    server)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image"
        ;;
    camera)
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image"
        ;;
    all)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image"
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image"
        ;;
    *)
        echo "Usage: $0 {server|camera|all}"
        exit 1
        ;;
esac

echo ""
echo ">>> Build complete!"
