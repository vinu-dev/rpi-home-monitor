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
HOTSPOT_IP="192.168.4.1/24"
HOTSPOT_SSID="HomeMonitor-Setup"
HOTSPOT_PASS="homemonitor"

start_hotspot() {
    echo "Starting WiFi hotspot: ${HOTSPOT_SSID}"

    # Remove any existing hotspot connection with this name
    nmcli connection delete "${CONN_NAME}" 2>/dev/null || true

    # Create the hotspot
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
        ipv4.method manual \
        ipv4.addresses "${HOTSPOT_IP}" \
        ipv4.never-default yes

    # Bring up the connection
    nmcli connection up "${CONN_NAME}"

    echo "Hotspot active on ${IFACE} — SSID: ${HOTSPOT_SSID}, IP: ${HOTSPOT_IP}"
}

stop_hotspot() {
    echo "Stopping WiFi hotspot: ${CONN_NAME}"

    # Bring down and remove the hotspot connection
    nmcli connection down "${CONN_NAME}" 2>/dev/null || true
    nmcli connection delete "${CONN_NAME}" 2>/dev/null || true

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
