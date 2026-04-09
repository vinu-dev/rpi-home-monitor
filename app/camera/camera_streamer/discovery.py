"""
mDNS service advertisement via Avahi.

Advertises the camera on the local network so the server
can auto-discover it.

Service: _rtsp._tcp
TXT records:
  id       = cam-<hardware-serial>
  version  = firmware version
  resolution = 1080p
  paired   = true/false
"""

# TODO: Implement DiscoveryService class
