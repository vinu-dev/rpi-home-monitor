#!/bin/sh
# REQ: SWR-036, SWR-050; RISK: RISK-010, RISK-012, RISK-018; SEC: SC-012, SC-019; TEST: TC-034, TC-044
# Notify camera runtime after WiFi link/DHCP changes so cached endpoints and
# mDNS advertisements do not remain pinned to stale LAN addresses.

IFACE="${1:-}"
ACTION="${2:-}"
EVENT_FILE="/data/config/network-event"

case "$IFACE" in
    wl*|wlan*) ;;
    *) exit 0 ;;
esac

case "$ACTION" in
    up|down|dhcp4-change|connectivity-change) ;;
    *) exit 0 ;;
esac

if [ ! -d /data/config ]; then
    exit 0
fi

printf '%s %s %s\n' "$(date +%s)" "$IFACE" "$ACTION" > "$EVENT_FILE" 2>/dev/null || true
systemctl try-restart avahi-daemon.service >/dev/null 2>&1 || true

if [ -f /data/.setup-done ]; then
    case "$ACTION" in
        up|dhcp4-change)
            systemctl try-restart camera-streamer.service >/dev/null 2>&1 || true
            ;;
    esac
fi
