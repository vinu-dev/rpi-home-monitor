#!/bin/sh
# REQ: SWR-024, SWR-050; RISK: RISK-012, RISK-018; SEC: SC-012, SC-019; TEST: TC-023, TC-044
# Recompute the server's canonical LAN mDNS identity after link/DHCP changes.

IFACE="${1:-}"
ACTION="${2:-}"

case "$IFACE" in
    eth*|en*|wl*|wlan*) ;;
    *) exit 0 ;;
esac

case "$ACTION" in
    up|down|dhcp4-change|connectivity-change) ;;
    *) exit 0 ;;
esac

if [ ! -d /data/config ]; then
    exit 0
fi

PYTHONPATH=/opt/monitor \
MONITOR_AVAHI_CONFIG=/data/config/avahi-daemon.conf \
    /usr/bin/python3 -m monitor.services.avahi_pin >/dev/null 2>&1 || true

systemctl try-restart avahi-daemon.service >/dev/null 2>&1 || true
