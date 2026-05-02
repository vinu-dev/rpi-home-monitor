#!/bin/sh
# REQ: SWR-021, SWR-024; RISK: RISK-010, RISK-012; SEC: SC-010, SC-012; TEST: TC-021, TC-023
# monitor-wifi-watchdog.sh — Fallback to setup hotspot if WiFi fails
#
# Runs once at boot after network-online.target.
# If wlan0 has no IP after 60 seconds, removes /data/.setup-done
# and starts the hotspot service so the user can reconfigure WiFi.
#
# Handles: router changed, password changed, moved to new location.

IFACE="wlan0"
TIMEOUT=60
SETUP_STAMP="/data/.setup-done"

# Only relevant if setup was previously completed
if [ ! -f "$SETUP_STAMP" ]; then
    echo "Setup not yet completed — watchdog not needed"
    exit 0
fi

echo "WiFi watchdog: checking wlan0 connectivity (timeout=${TIMEOUT}s)..."

WAITED=0
while [ "$WAITED" -lt "$TIMEOUT" ]; do
    # Get IPv4 address from wlan0
    IP=$(nmcli -t -f IP4.ADDRESS device show "$IFACE" 2>/dev/null | head -n 1 | cut -d: -f2 | cut -d/ -f1)
    if [ -n "$IP" ] && [ "$IP" != "0.0.0.0" ]; then
        echo "WiFi connected: ${IFACE} has IP ${IP} after ${WAITED}s"
        exit 0
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

echo "ERROR: No WiFi IP after ${TIMEOUT}s — reverting to setup mode"

# Remove setup stamp so hotspot service will start
rm -f "$SETUP_STAMP"
echo "Removed ${SETUP_STAMP}"

# Start the hotspot service (it has ConditionPathExists=!/data/.setup-done)
# We need to reset the condition check by restarting
systemctl start monitor-hotspot.service 2>/dev/null || true

# Also restart the monitor service so it enters setup wizard
systemctl restart monitor.service 2>/dev/null || true

echo "WiFi watchdog: hotspot started, setup wizard available"
