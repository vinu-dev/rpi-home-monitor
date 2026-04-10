#!/bin/sh
# monitor-hotspot.sh — WiFi hotspot management for initial device setup
# Usage: monitor-hotspot.sh start|stop|status
#
# Creates/destroys WiFi AP "HomeMonitor-Setup" for first-boot provisioning.
# The hotspot allows users to connect from a phone/laptop and configure
# WiFi credentials + admin password via the setup wizard.

set -e

CONN_NAME="HomeMonitor-Setup"
IFACE="wlan0"
HOTSPOT_SSID="HomeMonitor-Setup"
HOTSPOT_PASS="homemonitor"

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

start_hotspot() {
    echo "Starting WiFi hotspot: ${HOTSPOT_SSID}"

    # Check if WiFi interface exists
    if ! nmcli -t -f DEVICE device status 2>/dev/null | grep -q "^${IFACE}$"; then
        echo "WiFi interface ${IFACE} not found — skipping hotspot (ethernet-only setup)"
        exit 0
    fi

    # Remove any existing hotspot connection with this name
    nmcli connection delete "${CONN_NAME}" 2>/dev/null || true

    # Create the hotspot with shared mode (NetworkManager runs dnsmasq
    # automatically for DHCP when ipv4.method=shared, so connected
    # clients get an IP address in the 10.42.0.x range)
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

    # Bring up the connection
    nmcli connection up "${CONN_NAME}"

    # Get the actual IP assigned (shared mode uses 10.42.0.1 by default)
    ACTUAL_IP=$(nmcli -t -f IP4.ADDRESS dev show "${IFACE}" 2>/dev/null | head -n 1 | cut -d: -f2 | cut -d/ -f1)
    echo "Hotspot active on ${IFACE} — SSID: ${HOTSPOT_SSID}, IP: ${ACTUAL_IP:-10.42.0.1}"
    echo "Setup wizard available at http://${ACTUAL_IP:-10.42.0.1}/"
    echo "Captive portal: phone should auto-open setup page on connect"

    # LED: slow blink = setup mode
    led_setup_mode
}

stop_hotspot() {
    echo "Stopping WiFi hotspot: ${CONN_NAME}"

    # Bring down and remove the hotspot connection
    nmcli connection down "${CONN_NAME}" 2>/dev/null || true
    nmcli connection delete "${CONN_NAME}" 2>/dev/null || true

    # LED: solid on = normal operation
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
    *)
        echo "Usage: $0 {start|stop|status}" >&2
        exit 1
        ;;
esac
