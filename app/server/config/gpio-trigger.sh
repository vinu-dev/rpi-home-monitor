#!/bin/sh
# gpio-trigger.sh — Boot-time GPIO jumper detection for provisioning and factory reset
#
# Reads two GPIO pins at boot to determine if the operator has placed a jumper:
#   - Provisioning jumper (GPIO5, pin 29 → GND pin 30): re-enter setup wizard
#   - Factory reset jumper (GPIO27, pin 13 → GND pin 14): full data wipe + setup
#
# Both pins are active-low: HIGH = no jumper (normal boot), LOW = jumper present.
# GPIO5 has hardware pull-up at boot (BCM2711/BCM2710).
# GPIO27 requires gpio=27=ip,pu in config.txt for pull-up.
#
# Pin numbers are configurable via environment variables for platform abstraction:
#   GPIO_PROVISION_PIN  (default: 5)
#   GPIO_RESET_PIN      (default: 27)
#
# Usage: gpio-trigger.sh
# Exit codes:
#   0 — normal boot (no jumper detected)
#   10 — provisioning mode triggered (stamp deleted)
#   20 — factory reset triggered (data wiped)
#
# See ADR-0013 for design rationale.
set -e

# --- Configurable GPIO pins (platform abstraction) ---
PROVISION_PIN="${GPIO_PROVISION_PIN:-5}"
RESET_PIN="${GPIO_RESET_PIN:-27}"

SETUP_STAMP="/data/.setup-done"
GPIO_BASE="/sys/class/gpio"

# --- Helper functions ---

gpio_export() {
    PIN="$1"
    if [ ! -d "${GPIO_BASE}/gpio${PIN}" ]; then
        echo "$PIN" > "${GPIO_BASE}/export" 2>/dev/null || true
        # Wait for sysfs node to appear
        RETRIES=10
        while [ ! -d "${GPIO_BASE}/gpio${PIN}" ] && [ "$RETRIES" -gt 0 ]; do
            sleep 0.1
            RETRIES=$((RETRIES - 1))
        done
    fi
    # Ensure pin is input
    echo "in" > "${GPIO_BASE}/gpio${PIN}/direction" 2>/dev/null || true
}

gpio_read() {
    PIN="$1"
    if [ -f "${GPIO_BASE}/gpio${PIN}/value" ]; then
        cat "${GPIO_BASE}/gpio${PIN}/value"
    else
        echo "1"  # Default HIGH (no jumper) if sysfs unavailable
    fi
}

gpio_unexport() {
    PIN="$1"
    if [ -d "${GPIO_BASE}/gpio${PIN}" ]; then
        echo "$PIN" > "${GPIO_BASE}/unexport" 2>/dev/null || true
    fi
}

wipe_data() {
    echo "GPIO trigger: Factory reset — wiping /data contents"

    # Remove config files (preserve directory)
    CONFIG_DIR="/data/config"
    if [ -d "$CONFIG_DIR" ]; then
        rm -f "${CONFIG_DIR}/cameras.json" \
              "${CONFIG_DIR}/users.json" \
              "${CONFIG_DIR}/settings.json" \
              "${CONFIG_DIR}/.secret_key" \
              "${CONFIG_DIR}/camera.conf" 2>/dev/null || true
    fi

    # Remove directory trees
    for DIR in certs live recordings logs tailscale ota; do
        if [ -d "/data/${DIR}" ]; then
            rm -rf "/data/${DIR}"
            echo "  Removed /data/${DIR}"
        fi
    done

    # Remove setup stamp
    rm -f "$SETUP_STAMP" 2>/dev/null || true
    echo "  Removed $SETUP_STAMP"
}

# --- Main ---

echo "GPIO trigger: Checking provisioning (GPIO${PROVISION_PIN}) and reset (GPIO${RESET_PIN}) pins"

# Export and read both pins
gpio_export "$PROVISION_PIN"
gpio_export "$RESET_PIN"

# Small delay for value to stabilize after export
sleep 0.2

PROVISION_VAL=$(gpio_read "$PROVISION_PIN")
RESET_VAL=$(gpio_read "$RESET_PIN")

echo "GPIO trigger: provision_pin=GPIO${PROVISION_PIN} value=${PROVISION_VAL}, reset_pin=GPIO${RESET_PIN} value=${RESET_VAL}"

# Clean up GPIO exports
gpio_unexport "$PROVISION_PIN"
gpio_unexport "$RESET_PIN"

# Factory reset takes priority over provisioning-only
if [ "$RESET_VAL" = "0" ]; then
    echo "GPIO trigger: Factory reset jumper DETECTED (GPIO${RESET_PIN} LOW)"
    wipe_data

    # Wipe WiFi credentials via hotspot script if available
    for SCRIPT in /opt/monitor/scripts/monitor-hotspot.sh /opt/camera/scripts/camera-hotspot.sh; do
        if [ -x "$SCRIPT" ]; then
            "$SCRIPT" wipe 2>/dev/null || true
            break
        fi
    done

    echo "GPIO trigger: Factory reset complete — device will enter provisioning mode"
    exit 20

elif [ "$PROVISION_VAL" = "0" ]; then
    echo "GPIO trigger: Provisioning jumper DETECTED (GPIO${PROVISION_PIN} LOW)"
    # Only remove the stamp — data is preserved
    if [ -f "$SETUP_STAMP" ]; then
        rm -f "$SETUP_STAMP"
        echo "GPIO trigger: Removed $SETUP_STAMP — device will enter provisioning mode"
    else
        echo "GPIO trigger: $SETUP_STAMP already absent — device is already in provisioning mode"
    fi
    exit 10

else
    echo "GPIO trigger: No jumper detected — normal boot"
    exit 0
fi
