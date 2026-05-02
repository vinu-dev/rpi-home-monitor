# REQ: SWR-046, SWR-050; RISK: RISK-018, RISK-019; SEC: SC-018, SC-019; TEST: TC-043, TC-044
# Disable standalone dnsmasq service.
# NetworkManager launches its own internal dnsmasq instance
# when ipv4.method=shared is used (WiFi hotspot with DHCP).
# The standalone daemon would conflict by binding the same port.

SYSTEMD_AUTO_ENABLE = "disable"
