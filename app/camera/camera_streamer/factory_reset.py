"""
Factory reset service for camera — wipes config and returns to first-boot state.

Mirrors the server's FactoryResetService pattern (ADR-0013):
- Constructor injection (config, data_dir)
- WiFi credentials wiped via hotspot script's 'wipe' command
- System reboot after reset (systemd re-evaluates ConditionPathExists)

After reset: camera-hotspot.service starts (no .setup-done) → setup wizard.
"""

import logging
import os
import shutil
import subprocess
import threading

log = logging.getLogger("camera-streamer.factory-reset")

HOTSPOT_SCRIPT = "/opt/camera/scripts/camera-hotspot.sh"


class FactoryResetService:
    """Wipes camera data and restarts in first-boot state."""

    def __init__(
        self, config, data_dir: str = "/data", hotspot_script: str = HOTSPOT_SCRIPT
    ):
        self._config = config
        self._data_dir = data_dir
        self._hotspot_script = hotspot_script

    def execute_reset(self) -> tuple[str, int]:
        """Perform factory reset.

        Clears config, certs, logs. Wipes WiFi credentials.
        Schedules system reboot.

        Returns (message, status_code).
        """
        errors = []

        # 1. Remove setup-done stamp (re-enables provisioning wizard)
        stamp = os.path.join(self._data_dir, ".setup-done")
        self._safe_remove(stamp, errors)

        # 2. Remove config file
        config_path = os.path.join(self._data_dir, "config", "camera.conf")
        self._safe_remove(config_path, errors)

        # 3. Remove certificates (pairing data)
        certs_dir = os.path.join(self._data_dir, "certs")
        self._safe_rmtree(certs_dir, errors)

        # 4. Remove logs
        logs_dir = os.path.join(self._data_dir, "logs")
        self._safe_rmtree(logs_dir, errors)

        # 5. Remove OTA staging
        ota_dir = os.path.join(self._data_dir, "ota")
        self._safe_rmtree(ota_dir, errors)

        # 6. Clear WiFi credentials via hotspot script (ADR-0013)
        self._clear_wifi(errors)

        if errors:
            log.warning("Factory reset completed with errors: %s", errors)
        else:
            log.info("Factory reset completed successfully")

        # Schedule system reboot (full reboot ensures clean first-boot state)
        self._schedule_reboot()

        return "Factory reset complete. Restarting...", 200

    def _safe_remove(self, path: str, errors: list):
        """Remove a single file, ignoring if missing."""
        try:
            if os.path.exists(path):
                os.remove(path)
                log.debug("Removed: %s", path)
        except OSError as exc:
            log.warning("Failed to remove %s: %s", path, exc)
            errors.append(f"{path}: {exc}")

    def _safe_rmtree(self, path: str, errors: list):
        """Remove a directory tree, ignoring if missing."""
        try:
            if os.path.exists(path):
                shutil.rmtree(path)
                log.debug("Removed tree: %s", path)
        except OSError as exc:
            log.warning("Failed to remove %s: %s", path, exc)
            errors.append(f"{path}: {exc}")

    def _clear_wifi(self, errors: list):
        """Clear WiFi credentials via hotspot script's 'wipe' command."""
        try:
            if os.path.isfile(self._hotspot_script):
                result = subprocess.run(
                    [self._hotspot_script, "wipe"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode != 0:
                    log.warning(
                        "WiFi wipe returned non-zero: %s", result.stderr.strip()
                    )
                    errors.append(f"wifi: {result.stderr.strip()}")
                else:
                    log.debug("WiFi credentials wiped via hotspot script")
            else:
                log.debug(
                    "Hotspot script not found at %s — skipping WiFi wipe",
                    self._hotspot_script,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            log.warning("Failed to wipe WiFi credentials: %s", exc)
            errors.append(f"wifi: {exc}")

    def _schedule_reboot(self):
        """Reboot the system after a 2-second delay.

        A full reboot (not just service restart) is required so that
        camera-hotspot.service ConditionPathExists re-evaluates
        and starts the WiFi hotspot for first-boot setup.
        """

        def _do_reboot():
            log.info("Rebooting system for factory reset...")
            try:
                subprocess.run(
                    ["systemctl", "reboot"],
                    capture_output=True,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                log.error("System reboot failed: %s", exc)

        timer = threading.Timer(2.0, _do_reboot)
        timer.daemon = True
        timer.start()
