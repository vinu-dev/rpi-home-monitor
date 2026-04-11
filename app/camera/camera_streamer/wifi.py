"""
Shared WiFi utilities for camera-streamer.

Provides scan, connect, hotspot, and interface management functions
used by both WifiSetupServer (first boot) and CameraStatusServer
(post-setup). All functions take wifi_interface as a parameter
for platform abstraction.
"""

import logging
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
    """Set the system hostname and notify NetworkManager and Avahi."""
    try:
        subprocess.run(["hostname", hostname], capture_output=True, timeout=5)
        with open("/etc/hostname", "w") as f:
            f.write(hostname + "\n")
        subprocess.run(
            ["nmcli", "general", "hostname", hostname], capture_output=True, timeout=5
        )
        subprocess.run(
            ["systemctl", "restart", "avahi-daemon"], capture_output=True, timeout=10
        )
        log.info("Hostname set to %s", hostname)
        return True
    except Exception as e:
        log.warning("Failed to set hostname: %s", e)
        return False
