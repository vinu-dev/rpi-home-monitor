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
            # Last-resort fallback: restart the daemon. No goodbye for
            # the old name, but at least the new name reaches the wire
            # — strictly preserves the pre-fix behaviour.
            log.warning(
                "Falling back to avahi-daemon restart (no mDNS goodbye for %s)",
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
