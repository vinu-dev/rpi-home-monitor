#!/bin/sh
# camera-hotspot.sh — WiFi hotspot management for camera first-boot setup
# Usage: camera-hotspot.sh start|stop|status|connect|wipe
#
# Mirrors the server's monitor-hotspot.sh pattern exactly (ADR-0013).
# Creates/destroys WiFi AP "HomeCam-Setup" for first-boot provisioning.

set -e

CONN_NAME="HomeCam-Setup"
IFACE="wlan0"
HOTSPOT_SSID="HomeCam-Setup"
HOTSPOT_PASS="homecamera"

# --- LED control (ACT LED on RPi) ---
LED_PATH="/sys/class/leds/ACT"

led_write() {
    echo "$2" > "${LED_PATH}/$1" 2>/dev/null || true
}

led_setup_mode() {
    # Slow blink — waiting for setup
    chmod 0666 ${LED_PATH}/trigger ${LED_PATH}/brightness ${LED_PATH}/delay_on ${LED_PATH}/delay_off 2>/dev/null || true
    led_write trigger timer
    led_write delay_on 1000
    led_write delay_off 1000
}

led_connected() {
    # Solid on — running normally
    led_write trigger none
    led_write brightness 1
}

led_off() {
    led_write trigger none
    led_write brightness 0
}

wait_for_wifi() {
    MAX_WAIT=30
    WAITED=0
    echo "Waiting for WiFi interface ${IFACE} to be ready..."
    while [ "$WAITED" -lt "$MAX_WAIT" ]; do
        DEVTYPE=$(nmcli -t -f DEVICE,TYPE device status 2>/dev/null | grep "^${IFACE}:" | cut -d: -f2)
        if [ "$DEVTYPE" = "wifi" ]; then
            echo "WiFi interface ${IFACE} ready after ${WAITED}s"
            return 0
        fi
        sleep 1
        WAITED=$((WAITED + 1))
    done
    echo "WiFi interface ${IFACE} not ready after ${MAX_WAIT}s"
    return 1
}

start_hotspot() {
    echo "Starting WiFi hotspot: ${HOTSPOT_SSID}"

    if ! wait_for_wifi; then
        echo "WiFi interface ${IFACE} not found — skipping hotspot"
        exit 0
    fi

    # Remove any existing hotspot connection with this name
    nmcli connection delete "${CONN_NAME}" 2>/dev/null || true

    # Create the hotspot with shared mode (NM runs dnsmasq for DHCP)
    nmcli connection add \
        type wifi \
        ifname "${IFACE}" \
        con-name "${CONN_NAME}" \
        autoconnect no \
        ssid "${HOTSPOT_SSID}" \
        wifi.mode ap \
        wifi.band bg \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "${HOTSPOT_PASS}" \
        ipv4.method shared

    # Activate with retry (driver may still be initializing)
    MAX_RETRIES=5
    RETRY=0
    ACTIVATED=false
    set +e
    while [ "$RETRY" -lt "$MAX_RETRIES" ]; do
        OUTPUT=$(nmcli connection up "${CONN_NAME}" ifname "${IFACE}" 2>&1)
        if [ $? -eq 0 ]; then
            echo "$OUTPUT"
            ACTIVATED=true
            break
        fi
        RETRY=$((RETRY + 1))
        echo "Hotspot activation attempt ${RETRY}/${MAX_RETRIES} failed: ${OUTPUT}"
        echo "Retrying in 2s..."
        sleep 2
    done
    set -e

    if [ "$ACTIVATED" = false ]; then
        echo "ERROR: Failed to activate hotspot after ${MAX_RETRIES} attempts"
        exit 1
    fi

    ACTUAL_IP=$(nmcli -t -f IP4.ADDRESS dev show "${IFACE}" 2>/dev/null | head -n 1 | cut -d: -f2 | cut -d/ -f1)
    echo "Hotspot active on ${IFACE} — SSID: ${HOTSPOT_SSID}, IP: ${ACTUAL_IP:-10.42.0.1}"
    echo "Setup wizard available at http://${ACTUAL_IP:-10.42.0.1}/"

    led_setup_mode
}

stop_hotspot() {
    echo "Stopping WiFi hotspot: ${CONN_NAME}"

    nmcli connection down "${CONN_NAME}" 2>/dev/null || true
    nmcli connection delete "${CONN_NAME}" 2>/dev/null || true

    led_connected
    echo "Hotspot stopped"
}

status_hotspot() {
    if nmcli -t -f NAME connection show --active 2>/dev/null | grep -q "^${CONN_NAME}$"; then
        echo "Hotspot is active"
        exit 0
    else
        echo "Hotspot is not active"
        exit 1
    fi
}

connect_wifi() {
    # Usage: camera-hotspot.sh connect <ssid> <password>
    WIFI_SSID="$1"
    WIFI_PASS="$2"

    if [ -z "$WIFI_SSID" ] || [ -z "$WIFI_PASS" ]; then
        echo "Usage: $0 connect <ssid> <password>"
        exit 1
    fi

    echo "Connecting to WiFi: SSID=${WIFI_SSID}"

    # Stop hotspot first (can't be AP and client simultaneously)
    stop_hotspot 2>/dev/null || true
    sleep 2

    # Connect to target network
    set +e
    OUTPUT=$(nmcli dev wifi connect "${WIFI_SSID}" password "${WIFI_PASS}" ifname "${IFACE}" 2>&1)
    RC=$?
    set -e

    if [ $RC -eq 0 ]; then
        echo "WiFi connected: ${WIFI_SSID}"
        led_connected
        exit 0
    else
        echo "WiFi connection failed: ${OUTPUT}"
        # Restart hotspot for retry
        echo "Restarting hotspot for retry..."
        start_hotspot
        exit 1
    fi
}

wipe_wifi() {
    echo "Wiping all saved WiFi credentials"

    # Use nmcli to properly delete connections from NM's in-memory state
    # (just deleting files doesn't work — NM re-writes them on shutdown)
    nmcli -t -f NAME,TYPE con show 2>/dev/null | while IFS=: read -r NAME TYPE; do
        if [ "$TYPE" = "802-11-wireless" ]; then
            nmcli con delete "$NAME" 2>/dev/null && echo "  Deleted: $NAME" || true
        fi
    done

    # Clean up rootfs connection files
    NM_DIR="/etc/NetworkManager/system-connections"
    if [ -d "$NM_DIR" ]; then
        for CONN_FILE in "${NM_DIR}"/*; do
            if [ -f "$CONN_FILE" ]; then
                rm -f "$CONN_FILE"
                echo "  Removed file: $(basename "$CONN_FILE")"
            fi
        done
    fi

    # Clean up persistent /data connections (nm-persist.sh bind-mounts
    # /data/network/system-connections/ over /etc/NetworkManager/system-connections/
    # on every boot — wiping only /etc is not enough)
    PERSIST_DIR="/data/network/system-connections"
    if [ -d "$PERSIST_DIR" ]; then
        for CONN_FILE in "${PERSIST_DIR}"/*; do
            if [ -f "$CONN_FILE" ]; then
                rm -f "$CONN_FILE"
                echo "  Removed persistent: $(basename "$CONN_FILE")"
            fi
        done
    fi

    # Reset wpa_supplicant.conf to empty state
    WPA_CONF="/etc/wpa_supplicant.conf"
    if [ -f "$WPA_CONF" ]; then
        cat > "$WPA_CONF" <<'WPAEOF'
ctrl_interface=/var/run/wpa_supplicant
ctrl_interface_group=0
update_config=1
WPAEOF
        echo "  Reset wpa_supplicant.conf"
    fi

    echo "WiFi credentials wiped"
}

case "${1}" in
    start)
        start_hotspot
        ;;
    stop)
        stop_hotspot
        ;;
    status)
        status_hotspot
        ;;
    connect)
        connect_wifi "$2" "$3"
        ;;
    wipe)
        wipe_wifi
        ;;
    *)
        echo "Usage: $0 {start|stop|status|connect <ssid> <password>|wipe}" >&2
        exit 1
        ;;
esac
