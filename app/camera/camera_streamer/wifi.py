# REQ: SWR-036; RISK: RISK-012; SEC: SC-012; TEST: TC-034
"""
Shared WiFi utilities for camera-streamer.

Provides scan, connect, hotspot, and interface management functions
used by both WifiSetupServer (first boot) and CameraStatusServer
(post-setup). All functions take wifi_interface as a parameter
for platform abstraction.
"""

import logging
import os
import socket
import struct
import subprocess
import time

log = logging.getLogger("camera-streamer.wifi")

# Default hotspot settings
HOTSPOT_SSID = "HomeCam-Setup"
HOTSPOT_PASS = "homecamera"
HOTSPOT_CONN_NAME = "HomeCam-Setup"


def scan_networks(wifi_interface: str = "wlan0") -> list[dict]:
    """Scan for WiFi networks.

    Only works when interface is NOT in AP mode.
    Returns list of dicts with ssid, signal, security.
    """
    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan", "ifname", wifi_interface],
            capture_output=True,
            timeout=10,
        )
        time.sleep(3)

        result = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY",
                "device",
                "wifi",
                "list",
                "ifname",
                wifi_interface,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        networks = []
        seen = set()
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[0] and parts[0] not in seen:
                seen.add(parts[0])
                networks.append(
                    {
                        "ssid": parts[0],
                        "signal": int(parts[1]) if parts[1].isdigit() else 0,
                        "security": parts[2],
                    }
                )
        networks.sort(key=lambda n: n["signal"], reverse=True)
        return networks
    except Exception as e:
        log.error("WiFi scan failed: %s", e)
        return []


def connect_network(
    ssid: str, password: str, wifi_interface: str = "wlan0"
) -> tuple[bool, str]:
    """Connect to a WiFi network.

    Interface must NOT be in AP mode.
    Returns (success, error_message).
    """
    try:
        result = subprocess.run(
            [
                "nmcli",
                "device",
                "wifi",
                "connect",
                ssid,
                "password",
                password,
                "ifname",
                wifi_interface,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, ""
        err = result.stderr.strip() or result.stdout.strip()
        return False, err
    except subprocess.TimeoutExpired:
        return False, "Connection timed out"
    except Exception as e:
        return False, str(e)


def wait_for_interface(wifi_interface: str = "wlan0", max_wait: int = 30) -> bool:
    """Wait until WiFi interface is recognized by NetworkManager."""
    log.info("Waiting for WiFi interface %s to be ready...", wifi_interface)
    for waited in range(max_wait):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split(":")
                if (
                    len(parts) >= 2
                    and parts[0] == wifi_interface
                    and parts[1] == "wifi"
                ):
                    log.info(
                        "WiFi interface %s ready after %ds", wifi_interface, waited
                    )
                    return True
        except Exception:
            pass
        time.sleep(1)
    log.warning("WiFi interface %s not ready after %ds", wifi_interface, max_wait)
    return False


def start_hotspot(
    wifi_interface: str = "wlan0",
    ssid: str = HOTSPOT_SSID,
    password: str = HOTSPOT_PASS,
    conn_name: str = HOTSPOT_CONN_NAME,
) -> bool:
    """Start WiFi AP via NetworkManager.

    Returns True on success.
    """
    try:
        if not wait_for_interface(wifi_interface):
            log.warning("WiFi interface %s not found", wifi_interface)
            return False

        # Remove old connection
        subprocess.run(
            ["nmcli", "connection", "delete", conn_name],
            capture_output=True,
            timeout=10,
        )

        # Create AP with shared mode (auto dnsmasq DHCP)
        subprocess.run(
            [
                "nmcli",
                "connection",
                "add",
                "type",
                "wifi",
                "ifname",
                wifi_interface,
                "con-name",
                conn_name,
                "autoconnect",
                "no",
                "ssid",
                ssid,
                "wifi.mode",
                "ap",
                "wifi.band",
                "bg",
                "wifi-sec.key-mgmt",
                "wpa-psk",
                "wifi-sec.psk",
                password,
                "ipv4.method",
                "shared",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )

        # Activate with retry
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                subprocess.run(
                    ["nmcli", "connection", "up", conn_name, "ifname", wifi_interface],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=True,
                )
                break
            except subprocess.CalledProcessError as e:
                log.warning(
                    "Hotspot activation attempt %d/%d failed: %s",
                    attempt,
                    max_retries,
                    e.stderr.strip() if e.stderr else str(e),
                )
                if attempt >= max_retries:
                    raise
                time.sleep(2)

        log.info("Hotspot started: SSID=%s", ssid)
        return True

    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ) as e:
        log.error("Failed to start hotspot: %s", e)
        return False


def stop_hotspot(conn_name: str = HOTSPOT_CONN_NAME) -> None:
    """Stop and remove the hotspot connection."""
    try:
        subprocess.run(
            ["nmcli", "connection", "down", conn_name],
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["nmcli", "connection", "delete", conn_name],
            capture_output=True,
            timeout=10,
        )
        log.info("Hotspot stopped")
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        pass


def get_current_ssid() -> str:
    """Get the currently connected WiFi SSID."""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid", "device", "wifi"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in r.stdout.strip().splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].lower() == "yes":
                return parts[1]
    except Exception:
        pass
    return ""


def get_ip_address(wifi_interface: str = "wlan0") -> str:
    """Get the IP address of the WiFi interface."""
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", wifi_interface],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in r.stdout.strip().splitlines():
            if line.startswith("IP4.ADDRESS") and "/" in line:
                return line.split(":", 1)[1].split("/")[0]
    except Exception:
        pass
    return ""


def get_hostname() -> str:
    """Get the system hostname."""
    try:
        r = subprocess.run(
            ["hostname"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def set_hostname(hostname: str) -> bool:
    """Set the system hostname and notify Avahi with a proper goodbye.

    Uses transient hostname (memory-only) so it works on read-only rootfs.
    The hostname is saved to /data/config/hostname for persistence across
    reboots — the lifecycle restores it on every boot.

    mDNS goodbye contract (issue #200, RFC 6762 §10.1): when the
    hostname changes we MUST broadcast a record with TTL=0 for the
    previous name so cached resolvers around the network drop the
    stale entry immediately. The previous implementation did
    ``systemctl restart avahi-daemon`` which kills the daemon without
    giving it time to send goodbyes — operators chasing "the old name
    still resolves" wasted hours on dead leads.

    Approach: hand the new name to avahi-daemon over D-Bus via
    ``avahi-set-host-name``. The daemon owns the swap, broadcasts the
    cache-flush for the OLD name's A record (its self-publication),
    and announces the NEW name in the same transition. No daemon
    restart, no race with our own avahi-publish-* helpers running
    inside DiscoveryService.

    The goodbye is best-effort. If ``avahi-set-host-name`` is missing
    or returns non-zero, we still set the kernel hostname and fall
    back to a daemon restart so at least the new name reaches the
    wire. Failure of the goodbye does not block the hostname change.
    """
    try:
        # Snapshot the previous hostname so we can log the rotation
        # explicitly. avahi-daemon itself is what knew the old name —
        # we read socket.gethostname() purely for the log line.
        try:
            previous_hostname = socket.gethostname()
        except OSError:
            previous_hostname = ""

        # Set kernel hostname directly (works on read-only rootfs where
        # hostnamectl --transient is ignored due to static hostname in /etc)
        subprocess.run(["hostname", hostname], capture_output=True, timeout=5)
        # Save to /data for persistence across reboots
        data_hostname = "/data/config/hostname"
        try:
            os.makedirs(os.path.dirname(data_hostname), exist_ok=True)
            with open(data_hostname, "w") as f:
                f.write(hostname + "\n")
        except OSError:
            pass

        if previous_hostname and previous_hostname != hostname:
            log.info(
                "mDNS hostname change: %s -> %s (issuing goodbye + announce)",
                previous_hostname,
                hostname,
            )
        else:
            log.info("Hostname set to %s (no prior name to flush)", hostname)

        announced = _avahi_set_host_name(hostname)
        if not announced:
            # avahi-set-host-name needs D-Bus access to
            # org.freedesktop.Avahi.Server.SetHostName, which is gated
            # to root + user `avahi` by default — the camera-streamer
            # service runs as user `camera` and gets "Access denied"
            # (#233). The daemon-restart fallback alone doesn't send a
            # goodbye, leaving stale `<old>.local` cache entries on the
            # LAN. Send the goodbye directly via raw multicast UDP
            # before restarting the daemon — multicast/5353 is
            # privilege-free and satisfies #200's contract regardless
            # of avahi's D-Bus policy.
            if previous_hostname and previous_hostname != hostname:
                _broadcast_mdns_goodbye(
                    previous_hostname, get_ip_address() or "0.0.0.0"
                )

            log.warning(
                "Falling back to avahi-daemon restart for new-name announce "
                "(direct goodbye sent for %s if reachable)",
                previous_hostname or "<unknown>",
            )
            try:
                subprocess.run(
                    ["systemctl", "restart", "avahi-daemon"],
                    capture_output=True,
                    timeout=10,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
                log.warning("avahi-daemon restart fallback failed: %s", e)

        log.info("Hostname set to %s", hostname)
        return True
    except Exception as e:
        log.warning("Failed to set hostname: %s", e)
        return False


def _avahi_set_host_name(hostname: str) -> bool:
    """Tell avahi-daemon to swap to ``hostname`` over D-Bus.

    Returns True on success (daemon accepted the new name and will
    broadcast the goodbye for the old one + announce the new one).
    Returns False if the helper is missing, the daemon refused, or
    the call timed out — caller falls back to a daemon restart.

    Split out from ``set_hostname`` so the test suite can mock just
    the avahi side of the flow without re-stubbing kernel hostname /
    persistence calls.
    """
    try:
        result = subprocess.run(
            ["avahi-set-host-name", hostname],
            capture_output=True,
            timeout=5,
        )
    except FileNotFoundError:
        log.warning(
            "avahi-set-host-name not found — cannot send mDNS goodbye for old name"
        )
        return False
    except subprocess.TimeoutExpired:
        log.warning("avahi-set-host-name timed out — daemon may be stuck")
        return False
    except OSError as e:
        log.warning("avahi-set-host-name failed: %s", e)
        return False

    if result.returncode == 0:
        return True

    # avahi-set-host-name prints diagnostic on stderr — surface it so
    # operators know whether it was a name conflict, a permissions
    # issue, or the daemon rejecting the call.
    detail = (
        result.stderr.decode(errors="replace").strip()
        or result.stdout.decode(errors="replace").strip()
        or "<no output>"
    )
    log.warning(
        "avahi-set-host-name returned %d for %s: %s",
        result.returncode,
        hostname,
        detail,
    )
    return False


# ---------------------------------------------------------------------------
# Raw mDNS goodbye broadcast (#233)
#
# When avahi-set-host-name is unavailable (no D-Bus permission, daemon down)
# we need a privilege-free path to flush cached `<old>.local` bindings on the
# LAN. RFC 6762 §10.1 specifies a record with TTL=0; §10.2 specifies the
# cache-flush bit (high bit of the CLASS field) which forces resolvers to
# replace any existing matching records rather than merge with them. A single
# multicast UDP packet to 224.0.0.251:5353 carrying such a record satisfies
# the contract — and multicast send to a link-local group requires no special
# privilege.
# ---------------------------------------------------------------------------

# RFC 6762 §3 — IPv4 mDNS group + port.
_MDNS_GROUP_IPV4 = "224.0.0.251"
_MDNS_PORT = 5353
# RFC 6762 §11 — link-scoped multicast TTL must be 255.
_MDNS_TTL = 255


def _broadcast_mdns_goodbye(hostname: str, ip_address: str) -> bool:
    """Send a goodbye packet (TTL=0, cache-flush) for ``<hostname>.local``.

    Best-effort: multicast UDP gives no delivery acknowledgement, so we
    send twice ~1s apart per RFC 6762 §10's redundancy recommendation.
    Returns True if both sends went through, False on construction or
    socket errors. The caller logs and continues regardless — the
    goodbye is a hint to LAN caches, not a guarantee.

    The IP in the RDATA matters less than the cache-flush bit + TTL=0
    (which together tell resolvers to drop the binding). We pass the
    current IP when we have it for protocol cleanliness; ``0.0.0.0`` is
    accepted as a "we don't know" fallback.
    """
    if not hostname:
        return False
    label = hostname.removesuffix(".local").rstrip(".")
    if not label:
        return False

    try:
        ip_packed = socket.inet_aton(ip_address)
    except OSError:
        log.warning("mDNS goodbye: invalid IP %r — skipping", ip_address)
        return False

    try:
        packet = _build_mdns_goodbye_packet(label, ip_packed)
    except ValueError as e:
        log.warning("mDNS goodbye: cannot build packet for %s: %s", label, e)
        return False

    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, _MDNS_TTL)
        # Two transmissions ~1s apart so a single dropped packet doesn't
        # leave the LAN with stale cache entries — same redundancy
        # avahi-daemon uses internally for goodbyes.
        sock.sendto(packet, (_MDNS_GROUP_IPV4, _MDNS_PORT))
        time.sleep(1)
        sock.sendto(packet, (_MDNS_GROUP_IPV4, _MDNS_PORT))
        log.info("mDNS goodbye broadcast for %s.local (rdata=%s)", label, ip_address)
        return True
    except OSError as e:
        log.warning("mDNS goodbye send failed for %s.local: %s", label, e)
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _build_mdns_goodbye_packet(label: str, ip_packed: bytes) -> bytes:
    """Wire-format a DNS response for ``<label>.local`` with TTL=0.

    Layout per RFC 1035 §4 / RFC 6762 §10:

        Header (12 bytes)
          Transaction ID = 0
          Flags          = 0x8400  (QR=1 response, AA=1 authoritative)
          QDCOUNT        = 0
          ANCOUNT        = 1
          NSCOUNT        = 0
          ARCOUNT        = 0

        Answer
          NAME      = <label>.<local>.<root>     (length-prefixed labels)
          TYPE      = 1                          (A)
          CLASS     = 0x8001                     (IN | cache-flush)
          TTL       = 0                          (goodbye)
          RDLENGTH  = 4
          RDATA     = ip_packed                  (4 bytes IPv4)

    Raises ValueError if any label exceeds the 63-byte DNS limit or
    contains characters outside ASCII (avahi's mDNS doesn't apply
    Punycode).
    """
    header = struct.pack(
        ">HHHHHH",
        0x0000,  # Transaction ID — 0 for unsolicited mDNS responses.
        0x8400,  # Flags: QR=1, AA=1, OPCODE=0, RCODE=0.
        0,  # QDCOUNT
        1,  # ANCOUNT
        0,  # NSCOUNT
        0,  # ARCOUNT
    )

    name_section = b""
    for part in (label, "local"):
        try:
            encoded = part.encode("ascii")
        except UnicodeEncodeError as e:
            raise ValueError(f"non-ASCII DNS label: {part!r}") from e
        if not encoded:
            raise ValueError(f"empty DNS label: {part!r}")
        if len(encoded) > 63:
            raise ValueError(f"DNS label exceeds 63 bytes: {part!r}")
        name_section += bytes([len(encoded)]) + encoded
    name_section += b"\x00"  # Root label terminator.

    # TYPE=1 (A), CLASS=0x8001 (IN | cache-flush), TTL=0, RDLENGTH=4.
    answer_meta = struct.pack(">HHIH", 1, 0x8001, 0, 4)

    if len(ip_packed) != 4:
        raise ValueError(f"IPv4 RDATA must be 4 bytes, got {len(ip_packed)}")

    return header + name_section + answer_meta + ip_packed
