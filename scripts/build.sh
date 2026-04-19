#!/usr/bin/env bash
# =============================================================
# build.sh — Clone layers, configure, build Yocto images, and
# package the matching .swu OTA bundles in a single command.
#
# Usage:
#   ./scripts/build.sh server-dev     — RPi 4B development image
#   ./scripts/build.sh server-prod    — RPi 4B production image
#   ./scripts/build.sh camera-dev     — RPi Zero 2W development image
#   ./scripts/build.sh camera-prod    — RPi Zero 2W production image
#   ./scripts/build.sh all-dev        — both boards, development
#   ./scripts/build.sh all-prod       — both boards, production
#
# Flags (any position after target):
#   --no-swu    Skip the .swu packaging step (just bitbake images).
#   --sign      Pass --sign to build-swu.sh (prod OTA signing — needs
#               $KEY_DIR/ota-signing.{key,crt}; auto-on for *-prod).
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
TARGET="${1:-server-dev}"; shift 2>/dev/null || true
BUILD_SWU=true
SIGN_SWU=""
for arg in "$@"; do
    case "$arg" in
        --no-swu) BUILD_SWU=false ;;
        --sign)   SIGN_SWU="--sign" ;;
        *)        echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done
case "$TARGET" in
    *-prod) [ -z "$SIGN_SWU" ] && SIGN_SWU="--sign" ;;
esac

KEY_DIR="${KEY_DIR:-$HOME/.monitor-keys}"
LOCAL_OTA_CERT="$KEY_DIR/ota-signing.crt"
GENERATED_CERT_DIR="$YOCTO_DIR/meta-home-monitor/recipes-support/swupdate/files/generated"
GENERATED_CERT="$GENERATED_CERT_DIR/swupdate-public.crt"

echo ">>> Working in: $YOCTO_DIR"
echo ">>> Release: $RELEASE"
echo ">>> CPUs: $NCPU"
echo ">>> Target: $TARGET"

stage_local_ota_cert() {
    local configdir=$1
    local builddir=$2
    local config_path="$YOCTO_DIR/config/$configdir/local.conf"
    local build_conf="$builddir/conf/local.conf"

    # Enforcement comes from two places:
    #   1. The machine-wide local.conf ("production" target sets it).
    #   2. The --sign flag on this script (developer opts into signing
    #      for a dev build to rehearse the production flow).
    local enforce=0
    if grep -q 'SWUPDATE_SIGNING.*=.*"1"' "$config_path" 2>/dev/null; then
        enforce=1
    fi
    if [ "$SIGN_SWU" = "--sign" ]; then
        enforce=1
    fi
    if [ "$enforce" = "0" ]; then
        return 0
    fi

    if [ ! -f "$LOCAL_OTA_CERT" ]; then
        echo ""
        echo "ERROR: signature verification is enabled for this build."
        echo "Missing local OTA signing certificate: $LOCAL_OTA_CERT"
        echo "Run './scripts/generate-ota-keys.sh' first to generate your own keypair."
        echo "Each user maintains their own keys — do not share them."
        echo ""
        exit 1
    fi

    mkdir -p "$GENERATED_CERT_DIR"
    cp "$LOCAL_OTA_CERT" "$GENERATED_CERT"
    chmod 0644 "$GENERATED_CERT"
    echo ">>> Staged local OTA verification cert for build:"
    echo "    $GENERATED_CERT"

    # Flip SWUPDATE_SIGNING on in the BUILD dir's local.conf — that's
    # the one bitbake reads. build_image already copied the template
    # from config/$configdir/local.conf into $builddir/conf/local.conf
    # before calling us; overwrite its SWUPDATE_SIGNING line (or append
    # if absent). Idempotent — we both remove any previous override and
    # rewrite the current value.
    if [ -n "$build_conf" ] && [ -f "$build_conf" ]; then
        echo ">>> Setting SWUPDATE_SIGNING = \"1\" in $build_conf"
        sed -i '/^SWUPDATE_SIGNING\s*=/d' "$build_conf"
        printf '\nSWUPDATE_SIGNING = "1"\n' >> "$build_conf"
    fi
}

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
    # Optional 5th arg: config filename inside config/$configdir/.
    # Defaults to local.conf (dev). Prod targets pass local.conf.prod,
    # which `require`s local.conf and overrides SWUPDATE_SIGNING=1.
    local conf_file=${5:-local.conf}
    local src_conf="$YOCTO_DIR/config/$configdir/$conf_file"
    if [ ! -f "$src_conf" ]; then
        echo "ERROR: config file not found: $src_conf" >&2
        exit 1
    fi

    echo ""
    echo "============================================"
    echo " Building: $image"
    echo " Board: $board"
    echo " Build dir: $builddir"
    echo " Config: $conf_file"
    echo " Cores: $NCPU"
    echo "============================================"
    echo ""

    # oe-init-build-env is not nounset-safe when BBSERVER is absent.
    set +u
    source "$YOCTO_DIR/poky/oe-init-build-env" "$builddir"
    set -u

    cp "$src_conf" "$builddir/conf/local.conf"
    # local.conf.prod uses `require local.conf` relative to itself. When
    # we copy it into build/conf/ under the name local.conf we'd end up
    # self-referencing. Resolve the `require` against the source dir so
    # the prod override stays loadable from the build tree.
    if [ "$conf_file" != "local.conf" ]; then
        sed -i "s|^require local\\.conf$|require $YOCTO_DIR/config/$configdir/local.conf|" \
            "$builddir/conf/local.conf"
    fi
    cp "$YOCTO_DIR/config/bblayers.conf" "$builddir/conf/bblayers.conf"
    stage_local_ota_cert "$configdir" "$builddir"

    sed -i "s/^BB_NUMBER_THREADS.*/BB_NUMBER_THREADS = \"$NCPU\"/" "$builddir/conf/local.conf"
    sed -i "s/^PARALLEL_MAKE.*/PARALLEL_MAKE = \"-j $NCPU\"/" "$builddir/conf/local.conf"

    bitbake "$image"
}

# Locate the rootfs.ext4.gz that build-swu.sh needs for a given target.
# Yocto writes a symlink like
#   tmp-glibc/deploy/images/<machine>/<image>-<machine>.rootfs.ext4.gz
# pointing at the timestamped real artifact.
rootfs_for() {
    local builddir=$1 machine=$2 image=$3
    echo "$builddir/tmp-glibc/deploy/images/$machine/$image-$machine.rootfs.ext4.gz"
}

# Package the .swu bundle for one image after bitbake succeeds.
# Target "server" or "camera" drives build-swu.sh's hw-compat key.
package_swu() {
    local target=$1 builddir=$2 machine=$3 image=$4
    local rootfs version
    rootfs=$(rootfs_for "$builddir" "$machine" "$image")
    if [ ! -e "$rootfs" ]; then
        echo "!!! rootfs not found at $rootfs — skipping .swu for $target"
        return 1
    fi
    version="dev-$(date +%Y%m%d-%H%M)"
    echo ""
    echo ">>> Packaging $target .swu ($version)"
    ( cd "$YOCTO_DIR" && \
      "$YOCTO_DIR/scripts/build-swu.sh" --target "$target" \
          --rootfs "$rootfs" --version "$version" $SIGN_SWU )
}

case "$TARGET" in
    server-dev|server)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image-dev"
        $BUILD_SWU && package_swu server "$YOCTO_DIR/build" "raspberrypi4-64" "home-monitor-image-dev"
        ;;
    server-prod)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image-prod" "local.conf.prod"
        $BUILD_SWU && package_swu server "$YOCTO_DIR/build" "raspberrypi4-64" "home-monitor-image-prod"
        ;;
    camera-dev|camera)
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image-dev"
        $BUILD_SWU && package_swu camera "$YOCTO_DIR/build-zero2w" "home-monitor-camera" "home-camera-image-dev"
        ;;
    camera-prod)
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image-prod" "local.conf.prod"
        $BUILD_SWU && package_swu camera "$YOCTO_DIR/build-zero2w" "home-monitor-camera" "home-camera-image-prod"
        ;;
    all-dev|all)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image-dev"
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image-dev"
        if $BUILD_SWU; then
            package_swu server "$YOCTO_DIR/build" "raspberrypi4-64" "home-monitor-image-dev"
            package_swu camera "$YOCTO_DIR/build-zero2w" "home-monitor-camera" "home-camera-image-dev"
        fi
        ;;
    all-prod)
        build_image "RPi 4B" "$YOCTO_DIR/build" "rpi4b" "home-monitor-image-prod" "local.conf.prod"
        build_image "RPi Zero 2W" "$YOCTO_DIR/build-zero2w" "zero2w" "home-camera-image-prod" "local.conf.prod"
        if $BUILD_SWU; then
            package_swu server "$YOCTO_DIR/build" "raspberrypi4-64" "home-monitor-image-prod"
            package_swu camera "$YOCTO_DIR/build-zero2w" "home-monitor-camera" "home-camera-image-prod"
        fi
        ;;
    *)
        echo "Usage: $0 {server-dev|server-prod|camera-dev|camera-prod|all-dev|all-prod} [--no-swu] [--sign]"
        echo ""
        echo "  server-dev   RPi 4B development (debug-tweaks, root SSH)"
        echo "  server-prod  RPi 4B production (hardened, no root)"
        echo "  camera-dev   Zero 2W development"
        echo "  camera-prod  Zero 2W production"
        echo "  all-dev      Both boards, development"
        echo "  all-prod     Both boards, production"
        echo ""
        echo "Flags:"
        echo "  --no-swu     Build only the Yocto image, skip .swu packaging."
        echo "  --sign       Sign the .swu (auto-on for *-prod; needs keys in \$KEY_DIR)."
        echo ""
        echo "Legacy aliases: server=server-dev, camera=camera-dev, all=all-dev"
        exit 1
        ;;
esac

echo ""
echo ">>> Build complete!"
