"""
Camera discovery service — finds cameras on the local network via mDNS.

Responsibilities:
- Browse for _rtsp._tcp services using Avahi/dbus
- Detect new cameras → add to pending list
- Monitor paired cameras → update online/offline status
- Track camera firmware version from TXT records
- Trigger audit log entries for camera state changes

Camera considered offline after 30 seconds with no mDNS response.
"""

# TODO: Implement DiscoveryService class
