---
title: Network Fallback Guide
status: active
audience: [human, ai]
owner: engineering
source_of_truth: false
---

# What To Do When `.local` Does Not Work

If `homemonitor.local` or your camera's `*.local` address stops resolving on a
phone, tablet, or laptop, use the IP fallback links shown in the product UI.

## Camera

1. Open the camera setup-complete page or the camera status page.
2. Look for the **By IP** link under **Reach this camera**.
3. Copy the URL or scan the QR code.
4. Bookmark the IP URL on the current WiFi network.

The hostname link is still shown for convenience, but the IP URL is the stable
fallback when multicast DNS is blocked by the router or client device.

## Server

1. Open the server login page or the dashboard.
2. Use the **Server address** card to copy the URL or scan the QR code.
3. Enter that IP URL into the camera setup form when the server hostname is not
   resolving reliably.

The server card always shows the address that worked for the current request
path, so it reflects the interface that is already reachable from your device.

## If Neither Page Opens

1. Confirm the phone or laptop is back on the same home WiFi as the camera and
   server.
2. If the camera is unavailable, reconnect to the setup hotspot and repeat the
   WiFi step.
3. If the server is unavailable, verify it is powered on and reachable from
   another device on the same network.

## Notes

- IP bookmarks can go stale if your router rotates DHCP leases. Revisit the
  product pages above to pick up the new IP if needed.
- Self-signed HTTPS certificate warnings are expected on first use for both the
  hostname and IP URLs.
