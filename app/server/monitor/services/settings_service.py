"""
Settings service — system configuration management.

Single responsibility: settings validation, WiFi operations (post-setup).
Routes in api/settings.py are thin HTTP adapters that delegate here.

Design:
- Constructor injection (store, audit)
- All subprocess calls for nmcli live here (not in routes)
- Fail-silent audit logging
"""

import logging
import subprocess
import time

from flask import current_app

log = logging.getLogger("monitor.services.settings_service")


def _live_firmware_version() -> str:
    """Read the live release version from /etc/os-release.

    Defers the import so unit tests that don't ship the helper on
    PYTHONPATH still load this module. The helper itself is
    lazy-cached so repeated calls are essentially free.
    """
    from monitor.release_version import release_version

    return release_version()


UPDATABLE_FIELDS = {
    "timezone",
    "ntp_mode",
    "storage_threshold_percent",
    "clip_duration_seconds",
    "session_timeout_minutes",
    "hostname",
    "tailscale_enabled",
    "tailscale_auto_connect",
    "tailscale_accept_routes",
    "tailscale_ssh",
    "tailscale_auth_key",
    # ADR-0017: loop recording watermarks
    "loop_low_watermark_percent",
    "loop_hysteresis_percent",
}


# REQ: SWR-024; RISK: RISK-012; SEC: SC-012; TEST: TC-023
class SettingsService:
    """Manages system settings and WiFi configuration."""

    def __init__(self, store, audit=None):
        self._store = store
        self._audit = audit

    def get_settings(self) -> dict:
        """Return current system settings as a dict."""
        settings = self._store.get_settings()
        return {
            "timezone": settings.timezone,
            "ntp_mode": settings.ntp_mode,
            "storage_threshold_percent": settings.storage_threshold_percent,
            "clip_duration_seconds": settings.clip_duration_seconds,
            "session_timeout_minutes": settings.session_timeout_minutes,
            "hostname": settings.hostname,
            "setup_completed": settings.setup_completed,
            # Always serve the live release version from /etc/os-release
            # (via the shared release_version() helper) rather than the
            # persisted Settings.firmware_version field. The persisted
            # field is legacy plumbing kept for store-schema stability;
            # the truth lives in /etc/os-release per
            # docs/architecture/versioning.md §C.
            "firmware_version": _live_firmware_version(),
            "tailscale_enabled": settings.tailscale_enabled,
            "tailscale_auto_connect": settings.tailscale_auto_connect,
            "tailscale_accept_routes": settings.tailscale_accept_routes,
            "tailscale_ssh": settings.tailscale_ssh,
            "tailscale_has_auth_key": bool(settings.tailscale_auth_key),
            "loop_low_watermark_percent": settings.loop_low_watermark_percent,
            "loop_hysteresis_percent": settings.loop_hysteresis_percent,
        }

    def update_settings(
        self,
        data: dict,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        """Update system settings.

        Returns (message, status_code).
        """
        if not data:
            return "No updatable fields provided", 400

        # Validate: only known fields allowed
        unknown = set(data.keys()) - UPDATABLE_FIELDS
        if unknown:
            return f"Unknown fields: {', '.join(sorted(unknown))}", 400

        # Validate field values
        errors = self._validate(data)
        if errors:
            return errors[0], 400

        settings = self._store.get_settings()
        for key, value in data.items():
            setattr(settings, key, value)
        self._store.save_settings(settings)
        self._apply_runtime_changes(settings, set(data.keys()))

        self._log_audit(
            "SETTINGS_UPDATED",
            requesting_user,
            requesting_ip,
            f"updated: {', '.join(sorted(data.keys()))}",
        )

        return "Settings updated", 200

    def _apply_runtime_changes(self, settings, updated_fields: set[str]):
        """Apply settings that affect the running process immediately."""
        if "timezone" in updated_fields:
            self._apply_timezone(settings.timezone)

        if "ntp_mode" in updated_fields:
            self._apply_ntp_mode(settings.ntp_mode)

        if "session_timeout_minutes" in updated_fields:
            current_app.config["SESSION_TIMEOUT_MINUTES"] = (
                settings.session_timeout_minutes
            )

        if "clip_duration_seconds" in updated_fields:
            current_app.config["CLIP_DURATION_SECONDS"] = settings.clip_duration_seconds
            streaming = getattr(current_app, "streaming", None)
            if streaming:
                streaming.set_clip_duration(settings.clip_duration_seconds)

        if "storage_threshold_percent" in updated_fields:
            current_app.config["STORAGE_THRESHOLD_PERCENT"] = (
                settings.storage_threshold_percent
            )
            storage_manager = getattr(current_app, "storage_manager", None)
            if storage_manager:
                storage_manager.set_threshold_percent(
                    settings.storage_threshold_percent
                )

        # ADR-0017: push loop-recording watermarks to the running LoopRecorder
        if (
            "loop_low_watermark_percent" in updated_fields
            or "loop_hysteresis_percent" in updated_fields
        ):
            loop_recorder = getattr(current_app, "loop_recorder", None)
            if loop_recorder and hasattr(loop_recorder, "set_watermarks"):
                loop_recorder.set_watermarks(
                    low=settings.loop_low_watermark_percent,
                    hysteresis=settings.loop_hysteresis_percent,
                )

    # ------------------------------------------------------------------
    # Time / NTP helpers (ADR-0019)
    # ------------------------------------------------------------------
    def _apply_timezone(self, tz: str) -> None:
        """Apply a timezone via timedatectl. Best-effort; logs on failure."""
        try:
            subprocess.run(
                ["timedatectl", "set-timezone", tz],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Failed to apply timezone %r: %s", tz, e)

    def _apply_ntp_mode(self, mode: str) -> None:
        """Enable/disable automatic NTP sync."""
        flag = "true" if mode == "auto" else "false"
        try:
            subprocess.run(
                ["timedatectl", "set-ntp", flag],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Failed to set NTP mode %r: %s", mode, e)

    def get_time_status(self) -> dict:
        """Return current system time + NTP state (via timedatectl)."""
        settings = self._store.get_settings()
        info = {
            "timezone": settings.timezone,
            "ntp_mode": settings.ntp_mode,
            "ntp_active": False,
            "ntp_synchronized": False,
            "system_time": "",
            "rtc_time": "",
        }
        try:
            result = subprocess.run(
                [
                    "timedatectl",
                    "show",
                    "-p",
                    "Timezone",
                    "-p",
                    "NTP",
                    "-p",
                    "NTPSynchronized",
                    "-p",
                    "TimeUSec",
                    "-p",
                    "RTCTimeUSec",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            for line in result.stdout.splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k == "Timezone":
                    info["timezone"] = v or info["timezone"]
                elif k == "NTP":
                    info["ntp_active"] = v == "yes"
                elif k == "NTPSynchronized":
                    info["ntp_synchronized"] = v == "yes"
                elif k == "TimeUSec":
                    info["system_time"] = v
                elif k == "RTCTimeUSec":
                    info["rtc_time"] = v
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Failed to read time status: %s", e)
        return info

    def set_manual_time(
        self, iso_time: str, requesting_user: str = "", requesting_ip: str = ""
    ) -> tuple[str, int]:
        """Set system clock to `iso_time` (only allowed when ntp_mode=manual).

        Returns (message, status_code).
        """
        settings = self._store.get_settings()
        if settings.ntp_mode != "manual":
            return "Manual time can only be set when ntp_mode=manual", 409

        if not isinstance(iso_time, str) or "T" not in iso_time:
            return "time must be an ISO-8601 string (YYYY-MM-DDTHH:MM:SS)", 400

        # timedatectl accepts "YYYY-MM-DD HH:MM:SS"
        stamp = iso_time.replace("T", " ").rstrip("Z").split(".", 1)[0]

        try:
            result = subprocess.run(
                ["timedatectl", "set-time", stamp],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "timedatectl failed").strip()
                return err, 500
        except (OSError, subprocess.SubprocessError) as e:
            return str(e), 500

        self._log_audit(
            "TIME_SET_MANUAL",
            requesting_user,
            requesting_ip,
            f"system time set to {stamp}",
        )
        return "System time updated", 200

    def reapply_persisted_time_settings(self) -> None:
        """Re-apply timezone + NTP mode from persisted settings.

        Called on server startup so an OTA rootfs swap (which resets
        /etc/timezone + /etc/systemd/timesyncd.conf to factory defaults)
        is transparent to the user.
        """
        settings = self._store.get_settings()
        self._apply_timezone(settings.timezone)
        self._apply_ntp_mode(settings.ntp_mode)

    def get_wifi_status(self) -> dict:
        """Return current WiFi SSID and available networks."""
        return {
            "current_ssid": self._get_current_ssid(),
            "networks": self._scan_wifi_networks(),
        }

    def connect_wifi(
        self,
        ssid: str,
        password: str,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        """Connect to a WiFi network.

        Returns (message, status_code).
        """
        ssid = (ssid or "").strip()
        if not ssid:
            return "ssid is required", 400
        if not password:
            return "password is required", 400

        ok, err = self._do_wifi_connect(ssid, password)
        if ok:
            self._log_audit(
                "WIFI_CHANGED",
                requesting_user,
                requesting_ip,
                f"connected to: {ssid}",
            )
            return f"Connected to {ssid}", 200
        else:
            return err or "Connection failed", 500

    def _get_current_ssid(self) -> str:
        """Get the SSID of the currently connected WiFi network."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "active,ssid", "device", "wifi"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split(":", 1)
                if len(parts) == 2 and parts[0].lower() == "yes":
                    return parts[1]
        except Exception as e:
            log.warning("Failed to get current SSID: %s", e)
        return ""

    def _scan_wifi_networks(self) -> list[dict]:
        """Scan for available WiFi networks using nmcli."""
        try:
            subprocess.run(
                ["nmcli", "device", "wifi", "rescan"],
                capture_output=True,
                timeout=10,
            )
            time.sleep(2)

            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            networks = []
            seen = set()
            for line in result.stdout.strip().splitlines():
                parts = line.split(":", 2)
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
            log.warning("WiFi scan failed: %s", e)
            return []

    def _do_wifi_connect(self, ssid: str, password: str) -> tuple[bool, str]:
        """Connect to a WiFi network. Returns (ok, error_message)."""
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
                    "wlan0",
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

    def _validate(self, data: dict) -> list[str]:
        """Validate setting values. Returns list of error messages."""
        errors = []

        if "storage_threshold_percent" in data:
            val = data["storage_threshold_percent"]
            if not isinstance(val, int) or val < 50 or val > 99:
                errors.append(
                    "storage_threshold_percent must be an integer between 50 and 99"
                )

        if "clip_duration_seconds" in data:
            val = data["clip_duration_seconds"]
            if not isinstance(val, int) or val < 30 or val > 600:
                errors.append(
                    "clip_duration_seconds must be an integer between 30 and 600"
                )

        if "session_timeout_minutes" in data:
            val = data["session_timeout_minutes"]
            if not isinstance(val, int) or val < 5 or val > 1440:
                errors.append(
                    "session_timeout_minutes must be an integer between 5 and 1440"
                )

        if "hostname" in data:
            val = data["hostname"]
            if not isinstance(val, str) or len(val) < 1 or len(val) > 63:
                errors.append("hostname must be a string between 1 and 63 characters")

        if "timezone" in data:
            val = data["timezone"]
            if not isinstance(val, str) or len(val) < 1 or "/" not in val:
                errors.append(
                    "timezone must be a valid timezone string (e.g., Europe/Dublin)"
                )

        if "ntp_mode" in data:
            val = data["ntp_mode"]
            if val not in ("auto", "manual"):
                errors.append("ntp_mode must be 'auto' or 'manual'")

        for field in (
            "tailscale_enabled",
            "tailscale_auto_connect",
            "tailscale_accept_routes",
            "tailscale_ssh",
        ):
            if field in data and not isinstance(data[field], bool):
                errors.append(f"{field} must be a boolean")

        if "tailscale_auth_key" in data:
            val = data["tailscale_auth_key"]
            if not isinstance(val, str):
                errors.append("tailscale_auth_key must be a string")
            elif len(val) > 256:
                errors.append("tailscale_auth_key must be at most 256 characters")

        # ADR-0017: loop-recording watermarks. Low must be in [1, 50];
        # hysteresis in [1, 50]; low + hysteresis must stay < 100 so the
        # deletion target is reachable.
        low = data.get("loop_low_watermark_percent")
        hys = data.get("loop_hysteresis_percent")
        if "loop_low_watermark_percent" in data and (
            not isinstance(low, int) or low < 1 or low > 50
        ):
            errors.append(
                "loop_low_watermark_percent must be an integer between 1 and 50"
            )
        if "loop_hysteresis_percent" in data and (
            not isinstance(hys, int) or hys < 1 or hys > 50
        ):
            errors.append("loop_hysteresis_percent must be an integer between 1 and 50")
        # Cross-field: if either is being updated, ensure the pair sums < 100
        if "loop_low_watermark_percent" in data or "loop_hysteresis_percent" in data:
            settings = self._store.get_settings()
            new_low = (
                low
                if "loop_low_watermark_percent" in data
                else (settings.loop_low_watermark_percent)
            )
            new_hys = (
                hys
                if "loop_hysteresis_percent" in data
                else (settings.loop_hysteresis_percent)
            )
            if (
                isinstance(new_low, int)
                and isinstance(new_hys, int)
                and new_low + new_hys >= 100
            ):
                errors.append(
                    "loop_low_watermark_percent + loop_hysteresis_percent must be < 100"
                )

        return errors

    def _log_audit(self, event: str, user: str, ip: str, detail: str):
        """Log an audit event. Never raises."""
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception:
            log.debug("Audit log failed for %s (non-fatal)", event)
