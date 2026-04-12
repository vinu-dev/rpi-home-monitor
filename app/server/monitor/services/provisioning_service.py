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
import threading

log = logging.getLogger("monitor.services.provisioning_service")

HOTSPOT_SCRIPT = "/opt/monitor/scripts/monitor-hotspot.sh"


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

    def set_admin_password(self, password: str) -> tuple[str, int]:
        """Set the admin user's password.

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

        from monitor.auth import hash_password

        admin.password_hash = hash_password(password)
        self._store.save_user(admin)

        return "Admin password updated", 200

    def complete_setup(self) -> tuple[dict | None, str, int]:
        """Apply all settings and finish setup.

        Connects to WiFi, writes stamp file, schedules hotspot shutdown.
        Returns (result_dict, error_message, status_code).
        """
        if self.is_setup_complete():
            return None, "Setup already completed", 403

        ssid = self._pending_wifi.get("ssid", "")
        password = self._pending_wifi.get("password", "")

        if not ssid or not password:
            return (
                None,
                "WiFi credentials not saved. Go back and enter WiFi details.",
                400,
            )

        # Step 1: Connect to WiFi
        log.info("Connecting to WiFi: SSID=%s", ssid)
        ok, err = self._connect_wifi(ssid, password)
        if not ok:
            return None, err, 500

        # Step 2: Get new IP address
        ip_address = self._get_wifi_ip()

        # Step 3: Write stamp file
        stamp_err = self._write_stamp_file()
        if stamp_err:
            return None, stamp_err, 500

        # Clear credentials from memory
        self._pending_wifi["ssid"] = ""
        self._pending_wifi["password"] = ""

        # Step 4: Schedule delayed hotspot stop
        self._schedule_hotspot_stop()

        hostname = socket.gethostname()
        mdns_address = f"{hostname}.local"

        log.info("Setup complete! WiFi IP: %s", ip_address or "unknown")

        return (
            {
                "message": "Setup complete",
                "ip": ip_address,
                "hostname": mdns_address,
            },
            "",
            200,
        )

    def _connect_wifi(self, ssid: str, password: str) -> tuple[bool, str]:
        """Connect to a WiFi network. Returns (success, error_message)."""
        try:
            result = subprocess.run(
                [
                    "nmcli",
                    "dev",
                    "wifi",
                    "connect",
                    ssid,
                    "password",
                    password,
                    "ifname",
                    "wlan0",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return (
                False,
                "WiFi connection timed out. Check your password and try again.",
            )
        except (FileNotFoundError, OSError) as exc:
            return False, f"WiFi connection failed: {exc}"

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if (
                "secrets were required" in stderr.lower()
                or "no suitable" in stderr.lower()
            ):
                return False, "Incorrect WiFi password. Go back and try again."
            return (
                False,
                f"WiFi connection failed. Go back and try again. Detail: {stderr}",
            )

        log.info("WiFi connected to %s", ssid)
        return True, ""

    def _get_wifi_ip(self) -> str:
        """Get the IP address assigned to wlan0."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "IP4.ADDRESS", "dev", "show", "wlan0"],
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

    def _schedule_hotspot_stop(self):
        """Stop the hotspot after a 15-second delay."""

        def _delayed_stop():
            log.info("Delayed hotspot cleanup triggered (15s elapsed)")
            try:
                result = subprocess.run(
                    [HOTSPOT_SCRIPT, "stop"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                log.debug(
                    "Hotspot stop: rc=%d stdout=%s",
                    result.returncode,
                    result.stdout.strip(),
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                log.warning("Hotspot stop failed (non-fatal): %s", exc)

        timer = threading.Timer(15.0, _delayed_stop)
        timer.daemon = True
        timer.start()
