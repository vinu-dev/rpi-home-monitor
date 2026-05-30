"""
Provisioning service — first-boot setup wizard logic.

Single responsibility: WiFi scanning, credential management,
setup completion. Routes in provisioning.py are thin HTTP adapters.

Design:
- Constructor injection (store, data_dir)
- Subprocess calls for nmcli isolated here (not in routes)
- In-memory credential storage (never written to disk unencrypted)
- Fail-silent for all hardware operations
"""

import logging
import os
import socket
import subprocess

from monitor.services import privileged

log = logging.getLogger("monitor.services.provisioning_service")

HOTSPOT_SCRIPT = "/opt/monitor/scripts/monitor-hotspot.sh"
SERVER_HOSTNAME = "rpi-divinu"
BLOCKED_SETUP_HOTSPOT_PASSWORDS = {"homemonitor", "homecamera"}
MIN_SETUP_HOTSPOT_PASSWORD_LENGTH = 12
NETWORK_MODE_WIFI = "wifi"
NETWORK_MODE_ETHERNET = "ethernet"
VALID_NETWORK_MODES = {NETWORK_MODE_WIFI, NETWORK_MODE_ETHERNET}


# REQ: SWR-021, SWR-054; RISK: RISK-010; SEC: SC-010; TEST: TC-021
class ProvisioningService:
    """Manages first-boot setup: WiFi, admin password, completion."""

    def __init__(self, store, data_dir: str = "/data"):
        self._store = store
        self._data_dir = data_dir
        self._pending_wifi = {"ssid": "", "password": ""}

    @property
    def setup_done_path(self) -> str:
        """Path to the setup-done stamp file."""
        return os.path.join(self._data_dir, ".setup-done")

    def is_setup_complete(self) -> bool:
        """Check whether initial setup has already been completed."""
        return os.path.exists(self.setup_done_path)

    def is_hotspot_active(self) -> bool:
        """Check if the setup hotspot is currently active."""
        try:
            result = subprocess.run(
                [HOTSPOT_SCRIPT, "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def get_status(self) -> dict:
        """Return current setup state."""
        return {
            "setup_complete": self.is_setup_complete(),
            "hotspot_active": self.is_hotspot_active(),
        }

    def scan_wifi(self) -> tuple[list[dict], str, int]:
        """Scan for available WiFi networks.

        Returns (networks_list, error_message, status_code).
        """
        if self.is_setup_complete():
            return [], "Setup already completed", 403

        try:
            result = subprocess.run(
                [
                    "nmcli",
                    "-t",
                    "-f",
                    "SSID,SIGNAL,SECURITY",
                    "dev",
                    "wifi",
                    "list",
                    "--rescan",
                    "yes",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return [], "WiFi scan timed out", 504
        except (FileNotFoundError, OSError) as exc:
            return [], f"WiFi scan failed: {exc}", 500

        if result.returncode != 0:
            return [], f"WiFi scan failed: {result.stderr.strip()}", 500

        # Parse nmcli output, deduplicate, sort by signal
        networks = {}
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            ssid = parts[0].strip()
            if not ssid:
                continue
            try:
                signal = int(parts[1].strip())
            except (ValueError, IndexError):
                signal = 0
            security = parts[2].strip() if len(parts) >= 3 else ""

            if ssid not in networks or signal > networks[ssid]["signal"]:
                networks[ssid] = {
                    "ssid": ssid,
                    "signal": signal,
                    "security": security,
                }

        network_list = sorted(
            networks.values(), key=lambda n: n["signal"], reverse=True
        )
        return network_list, "", 200

    def save_wifi_credentials(self, ssid: str, password: str) -> tuple[str, int]:
        """Save WiFi credentials in memory for later use at /complete.

        Returns (message, status_code).
        """
        if self.is_setup_complete():
            return "Setup already completed", 403

        ssid = ssid.strip()
        password = password.strip()

        if not ssid:
            return "SSID is required", 400
        if not password:
            return "Password is required", 400

        self._pending_wifi["ssid"] = ssid
        self._pending_wifi["password"] = password

        log.info("WiFi credentials saved for SSID=%s", ssid)
        return f"WiFi credentials saved for {ssid}", 200

    @property
    def setup_hotspot_password_path(self) -> str:
        return os.path.join(self._data_dir, "config", "setup-hotspot.psk")

    def set_admin_password(
        self, password: str, setup_hotspot_password: str = ""
    ) -> tuple[str, int]:
        """Set the admin password and rotate the setup hotspot password.

        Returns (message, status_code).
        """
        if self.is_setup_complete():
            return "Setup already completed", 403

        from monitor.password_policy import validate_password

        pw_error = validate_password(password)
        if pw_error:
            return pw_error, 400

        admin = self._store.get_user_by_username("admin")
        if not admin:
            return "Default admin user not found", 500

        hotspot_error = self._save_setup_hotspot_password(setup_hotspot_password)
        if hotspot_error:
            return hotspot_error, 400

        from monitor.auth import hash_password

        admin.password_hash = hash_password(password)
        admin.must_change_password = False
        self._store.save_user(admin)

        return "Admin password updated", 200

    def complete_setup(
        self, network_mode: str = NETWORK_MODE_WIFI
    ) -> tuple[dict | None, str, int]:
        """Apply all settings and finish setup.

        Connects to WiFi or keeps the wired LAN path, writes the setup
        stamp file, and stops the setup hotspot once provisioning is complete.
        Returns (result_dict, error_message, status_code).
        """
        if self.is_setup_complete():
            return None, "Setup already completed", 403

        network_mode = str(network_mode or NETWORK_MODE_WIFI).strip().lower()
        if network_mode not in VALID_NETWORK_MODES:
            return None, "Network mode must be wifi or ethernet.", 400

        if network_mode == NETWORK_MODE_WIFI:
            ssid = self._pending_wifi.get("ssid", "")
            password = self._pending_wifi.get("password", "")
            if not ssid or not password:
                return (
                    None,
                    "WiFi credentials not saved. Go back and enter WiFi details.",
                    400,
                )

        if not os.path.isfile(self.setup_hotspot_password_path):
            return (
                None,
                "Set a new setup hotspot password before finishing setup.",
                400,
            )

        if network_mode == NETWORK_MODE_WIFI:
            log.info("Connecting to WiFi: SSID=%s", ssid)
            ok, err = self._connect_wifi(ssid, password)
            if not ok:
                return None, err, 500
            ip_address = self._get_wifi_ip()
        else:
            log.info("Completing setup using wired Ethernet")
            ip_address = self._get_ethernet_ip()
            if not ip_address:
                return (
                    None,
                    "Ethernet is not connected. Plug in Ethernet or choose WiFi.",
                    400,
                )

        # Step 3: Set hostname (ensures correct mDNS after factory reset)
        self._set_hostname(SERVER_HOSTNAME)

        # Step 4: Write stamp file
        stamp_err = self._write_stamp_file()
        if stamp_err:
            return None, stamp_err, 500

        # Clear credentials from memory
        self._pending_wifi["ssid"] = ""
        self._pending_wifi["password"] = ""

        if network_mode == NETWORK_MODE_ETHERNET:
            self._stop_hotspot()

        # When WiFi mode is used, hotspot was stopped by the connect command
        # in _connect_wifi() — no separate stop needed (ADR-0013).

        mdns_address = f"{SERVER_HOSTNAME}.local"

        log.info("Setup complete via %s. IP: %s", network_mode, ip_address or "unknown")

        return (
            {
                "message": "Setup complete",
                "ip": ip_address,
                "hostname": mdns_address,
                "network_mode": network_mode,
            },
            "",
            200,
        )

    def _connect_wifi(self, ssid: str, password: str) -> tuple[bool, str]:
        """Connect to WiFi via hotspot script.

        Uses the hotspot script's 'connect' command which atomically
        stops the AP and connects to the target network (ADR-0013).
        Returns (success, error_message).
        """
        if privileged.should_use_helper():
            try:
                data = privileged.request(
                    "hotspot.connect_wifi",
                    {"ssid": ssid, "password": password},
                    timeout=50,
                )
            except privileged.PrivilegedHelperError as exc:
                return False, f"WiFi connection failed: {exc}"
            if int(data.get("returncode") or 0) == 0:
                log.info("WiFi connected to %s via privileged helper", ssid)
                return True, ""
            output = str(data.get("stderr") or data.get("stdout") or "").strip()
            if "secrets" in output.lower() or "no suitable" in output.lower():
                return False, "Incorrect WiFi password. Go back and try again."
            return (
                False,
                f"WiFi connection failed. Go back and try again. Detail: {output}",
            )
        try:
            result = subprocess.run(
                [HOTSPOT_SCRIPT, "connect", ssid, password],
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired:
            return (
                False,
                "WiFi connection timed out. Check your password and try again.",
            )
        except (FileNotFoundError, OSError) as exc:
            return False, f"WiFi connection failed: {exc}"

        if result.returncode != 0:
            output = result.stderr.strip() or result.stdout.strip()
            if "secrets" in output.lower() or "no suitable" in output.lower():
                return False, "Incorrect WiFi password. Go back and try again."
            return (
                False,
                f"WiFi connection failed. Go back and try again. Detail: {output}",
            )

        log.info("WiFi connected to %s via hotspot script", ssid)
        return True, ""

    def _get_wifi_ip(self) -> str:
        """Get the IP address assigned to wlan0."""
        return self._get_device_ip("wlan0")

    def _get_ethernet_ip(self) -> str:
        """Return the first connected Ethernet IPv4 address, if known."""
        for device in self._connected_ethernet_devices():
            ip_address = self._get_device_ip(device)
            if ip_address:
                return ip_address
        return ""

    def _connected_ethernet_devices(self) -> list[str]:
        """List connected Ethernet devices as reported by NetworkManager."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return []
        if result.returncode != 0:
            return []

        devices: list[str] = []
        for line in result.stdout.strip().splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            device, dev_type, state = [part.strip() for part in parts[:3]]
            if device and dev_type == "ethernet" and state == "connected":
                devices.append(device)
        return devices

    def _get_device_ip(self, device: str) -> str:
        """Get the IPv4 address assigned to a NetworkManager device."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "IP4.ADDRESS", "dev", "show", device],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    if ":" in line:
                        addr = line.split(":", 1)[1].strip()
                        if "/" in addr:
                            addr = addr.split("/")[0]
                        if addr:
                            return addr
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return ""

    def _stop_hotspot(self) -> None:
        """Best-effort stop for the setup hotspot after Ethernet setup."""
        if privileged.should_use_helper():
            try:
                data = privileged.request("hotspot.stop", timeout=20)
            except privileged.PrivilegedHelperError as exc:
                log.warning("Failed to stop setup hotspot via helper: %s", exc)
                return
            if int(data.get("returncode") or 0) != 0:
                output = str(data.get("stderr") or data.get("stdout") or "").strip()
                log.warning("Setup hotspot stop returned non-zero: %s", output)
            return

        try:
            result = subprocess.run(
                [HOTSPOT_SCRIPT, "stop"],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            log.warning("Failed to stop setup hotspot: %s", exc)
            return
        if result.returncode != 0:
            output = result.stderr.strip() or result.stdout.strip()
            log.warning("Setup hotspot stop returned non-zero: %s", output)

    def _write_stamp_file(self) -> str:
        """Write the setup-done stamp file. Returns error message or empty string."""
        stamp = self.setup_done_path
        try:
            os.makedirs(os.path.dirname(stamp), exist_ok=True)
            with open(stamp, "w") as f:
                f.write("setup completed\n")
            log.info("Stamp file written: %s", stamp)
            return ""
        except OSError as exc:
            return f"Failed to mark setup complete: {exc}"

    def _save_setup_hotspot_password(self, password: str) -> str:
        password = str(password or "").strip()
        if password.lower() in BLOCKED_SETUP_HOTSPOT_PASSWORDS:
            return "Choose a new setup hotspot password, not the factory default"
        if len(password) < MIN_SETUP_HOTSPOT_PASSWORD_LENGTH:
            return (
                "Setup hotspot password must be at least "
                f"{MIN_SETUP_HOTSPOT_PASSWORD_LENGTH} characters"
            )
        try:
            path = self.setup_hotspot_password_path
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(password + "\n")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return ""
        except OSError as exc:
            return f"Failed to save setup hotspot password: {exc}"

    def _set_hostname(self, hostname: str):
        """Set system hostname for mDNS discovery.

        Sets both persistent and transient hostname (transient works on
        read-only rootfs). Also saves to /data/config/hostname so the
        hostname survives OTA rootfs updates.
        """
        current = socket.gethostname()
        if current == hostname:
            return
        try:
            if privileged.should_use_helper():
                privileged.request("hostname.set", {"hostname": hostname}, timeout=10)
            else:
                subprocess.run(
                    ["hostnamectl", "set-hostname", hostname],
                    capture_output=True,
                    timeout=10,
                )
            # Save to /data for persistence across OTA rootfs updates
            data_hostname = os.path.join(self._data_dir, "config", "hostname")
            try:
                with open(data_hostname, "w") as f:
                    f.write(hostname + "\n")
            except OSError:
                pass
            log.info("Hostname set: %s -> %s", current, hostname)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            log.warning("Failed to set hostname: %s", exc)
        except privileged.PrivilegedHelperError as exc:
            log.warning("Failed to set hostname via helper: %s", exc)

    def _get_hotspot_script(self) -> str:
        """Return path to the hotspot management script."""
        return HOTSPOT_SCRIPT
