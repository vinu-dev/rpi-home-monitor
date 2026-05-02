#!/usr/bin/env bash
# REQ: SWR-048, SWR-055; RISK: RISK-009; SEC: SC-009; TEST: TC-045
# =============================================================
# setup-env.sh — Set up a fresh Ubuntu machine for Yocto builds
#
# Run once on a fresh Ubuntu 22.04 or 24.04 installation.
# Installs all prerequisites for building Home Monitor OS images
# and running application unit tests.
#
# Usage:
#   ./scripts/setup-env.sh
#
# What it does:
#   1. Installs Yocto build dependencies (apt)
#   2. Installs Python app + test dependencies (pip)
#   3. Sets locale to en_US.UTF-8
#   4. Creates 8GB swap file (if missing)
#   5. Fixes Ubuntu 24.04 AppArmor restriction
#   6. Installs app packages in dev mode for testing
# =============================================================
set -euo pipefail

YOCTO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "============================================"
echo " Home Monitor OS — Build Environment Setup"
echo "============================================"
echo ""
echo "Directory: $YOCTO_DIR"
echo ""

# --- Step 1: Yocto build dependencies ---
echo ">>> [1/6] Installing Yocto build dependencies..."
sudo apt update
sudo apt install -y \
    gawk wget git diffstat unzip texinfo gcc build-essential chrpath socat \
    cpio python3 python3-pip python3-pexpect python3-venv xz-utils debianutils \
    iputils-ping python3-git python3-jinja2 libegl1 libsdl1.2-dev pylint xterm \
    python3-subunit mesa-common-dev zstd liblz4-tool lz4 libacl1 file locales

# --- Step 2: Python test dependencies ---
echo ""
echo ">>> [2/6] Installing Python test dependencies..."
pip3 install --user \
    pytest\>=8.0 \
    pytest-cov\>=5.0 \
    flask\>=3.0 \
    bcrypt\>=4.0 \
    jinja2\>=3.0

# Install app packages in dev mode (for pytest)
if [ -f "$YOCTO_DIR/app/server/setup.py" ]; then
    echo ">>> Installing monitor-server in dev mode..."
    pip3 install --user -e "$YOCTO_DIR/app/server"
fi
if [ -f "$YOCTO_DIR/app/camera/setup.py" ]; then
    echo ">>> Installing camera-streamer in dev mode..."
    pip3 install --user -e "$YOCTO_DIR/app/camera"
fi

# --- Step 3: Locale ---
echo ""
echo ">>> [3/6] Setting locale to en_US.UTF-8..."
sudo locale-gen en_US.UTF-8

# --- Step 4: Swap ---
echo ""
echo ">>> [4/6] Setting up swap (8GB)..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 8G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
    echo "    Swap created and enabled."
else
    echo "    Swap already exists, skipping."
fi

# --- Step 5: AppArmor fix for Ubuntu 24.04 ---
echo ""
echo ">>> [5/6] Checking AppArmor (Ubuntu 24.04 fix)..."
if grep -q "apparmor_restrict_unprivileged_userns" /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null; then
    CURRENT=$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns)
    if [ "$CURRENT" = "1" ]; then
        sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
        echo "kernel.apparmor_restrict_unprivileged_userns=0" | sudo tee /etc/sysctl.d/99-yocto.conf
        echo "    AppArmor restriction disabled for bitbake."
    else
        echo "    Already configured, skipping."
    fi
else
    echo "    Not applicable (not Ubuntu 24.04 or AppArmor not active)."
fi

# --- Step 6: Verify ---
echo ""
echo ">>> [6/6] Verifying installation..."

ERRORS=0

# Check key commands
for cmd in git gcc python3 pip3 pytest chrpath; do
    if command -v $cmd &>/dev/null; then
        echo "    OK: $cmd ($(command -v $cmd))"
    else
        echo "    MISSING: $cmd"
        ERRORS=$((ERRORS + 1))
    fi
done

# Check locale
if locale -a 2>/dev/null | grep -q "en_US.utf8"; then
    echo "    OK: en_US.UTF-8 locale"
else
    echo "    MISSING: en_US.UTF-8 locale"
    ERRORS=$((ERRORS + 1))
fi

# Check swap
SWAP_MB=$(free -m | awk '/Swap/ {print $2}')
if [ "$SWAP_MB" -ge 4000 ] 2>/dev/null; then
    echo "    OK: Swap ${SWAP_MB}MB"
else
    echo "    WARNING: Swap is only ${SWAP_MB}MB (recommend 8GB)"
fi

# Check disk
DISK_GB=$(df -BG "$YOCTO_DIR" | awk 'NR==2 {print $4}' | tr -d 'G')
if [ "$DISK_GB" -ge 100 ] 2>/dev/null; then
    echo "    OK: ${DISK_GB}GB free disk"
else
    echo "    WARNING: Only ${DISK_GB}GB free (recommend 200GB+)"
fi

echo ""
if [ $ERRORS -eq 0 ]; then
    echo "============================================"
    echo " Setup complete! Ready to build."
    echo "============================================"
    echo ""
    echo " Next steps:"
    echo "   ./scripts/build.sh server-dev     # Build server image"
    echo "   ./scripts/build.sh camera-dev     # Build camera image"
    echo ""
    echo " Run tests:"
    echo "   cd app/server && pytest"
    echo "   cd app/camera && pytest"
    echo ""
else
    echo "============================================"
    echo " Setup completed with $ERRORS error(s)."
    echo " Fix the issues above and re-run."
    echo "============================================"
    exit 1
fi
