# REQ: SWR-018; RISK: RISK-006; SEC: SC-006; TEST: TC-015
"""
Factory reset service for camera: wipes config and returns to first-boot state.

Mirrors the server reset pattern:
- Constructor injection (config, data_dir)
- WiFi credentials wiped via the hotspot script or root helper
- System reboot after reset so systemd re-evaluates setup-hotspot conditions

After reset: camera-hotspot.service starts (no .setup-done) and serves setup.
"""

import logging
import os
import shutil
import subprocess
import threading

from camera_streamer import privileged

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

        # 3. Preserve the operator-chosen setup hotspot password. A GUI
        # factory reset is an authenticated admin action; keeping this
        # credential avoids falling back to a public first-boot default and
        # lets the same operator re-enter setup after reboot.

        # 4. Remove certificates (pairing data)
        certs_dir = os.path.join(self._data_dir, "certs")
        self._safe_rmtree(certs_dir, errors)

        # 5. Remove logs
        logs_dir = os.path.join(self._data_dir, "logs")
        self._safe_rmtree(logs_dir, errors)

        # 6. Remove OTA staging
        ota_dir = os.path.join(self._data_dir, "ota")
        self._safe_rmtree(ota_dir, errors)

        # 7. Clear WiFi credentials via hotspot script/root helper.
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
        """Clear WiFi credentials via hotspot script plus direct /data cleanup.

        The hotspot script's wipe command handles NetworkManager deletion.
        Direct /data cleanup is retained as a safety net because nm-persist.sh
        restores connections from /data/network/system-connections/ on boot.
        """
        if privileged.should_use_helper():
            try:
                data = privileged.request("hotspot.wipe", timeout=35)
            except privileged.PrivilegedHelperError as exc:
                log.warning("Failed to wipe WiFi credentials via helper: %s", exc)
                errors.append(f"wifi: {exc}")
            else:
                if int(data.get("returncode") or 0) != 0:
                    output = str(data.get("stderr") or data.get("stdout") or "")
                    output = output.strip()
                    log.warning("WiFi wipe returned non-zero: %s", output)
                    errors.append(f"wifi: {output}")
                else:
                    log.debug("WiFi credentials wiped via privileged helper")
        else:
            self._clear_wifi_without_helper(errors)

        persist_dir = os.path.join(self._data_dir, "network", "system-connections")
        self._wipe_dir_contents(persist_dir, "persistent WiFi", errors)

        marker = os.path.join(self._data_dir, "network", ".wifi-wiped")
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w") as f:
                f.write("1\n")
            log.debug("WiFi wipe marker written: %s", marker)
        except OSError as exc:
            log.warning("Failed to write wifi wipe marker: %s", exc)
            errors.append(f"wifi-marker: {exc}")

    def _clear_wifi_without_helper(self, errors: list) -> None:
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
                    "Hotspot script not found at %s; skipping script wipe",
                    self._hotspot_script,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            log.warning("Failed to wipe WiFi credentials: %s", exc)
            errors.append(f"wifi: {exc}")

    def _wipe_dir_contents(self, dirpath: str, label: str, errors: list):
        """Remove all files in a directory, not the directory itself."""
        if not os.path.isdir(dirpath):
            return
        for fname in os.listdir(dirpath):
            fpath = os.path.join(dirpath, fname)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
                    log.debug("Removed %s: %s", label, fname)
            except OSError as exc:
                log.warning("Failed to remove %s: %s", fpath, exc)
                errors.append(f"{label}: {exc}")

    def _schedule_reboot(self):
        """Recover into first-boot setup after a 2-second delay."""
        timer = threading.Timer(2.0, self._run_post_reset_recovery)
        timer.daemon = True
        timer.start()

    def _run_post_reset_recovery(self):
        """Reboot after reset, or explicitly bring setup networking back."""
        if self._attempt_reboot():
            return
        log.error("Factory reset reboot failed; starting setup hotspot fallback")
        if not self._start_setup_hotspot():
            log.critical(
                "Factory reset could not reboot or start the setup hotspot; "
                "operator intervention is required"
            )

    def _attempt_reboot(self) -> bool:
        log.info("Rebooting system for factory reset...")
        if privileged.should_use_helper():
            try:
                data = privileged.request("system.reboot", timeout=20)
            except privileged.PrivilegedHelperError as exc:
                log.error("Privileged reboot failed: %s", exc)
                return False
            return self._command_result_ok(data, "system.reboot")
        ok, error = self._run_system_command(["systemctl", "reboot"], timeout=30)
        if not ok:
            log.error("System reboot failed: %s", error)
        return ok

    def _start_setup_hotspot(self) -> bool:
        if privileged.should_use_helper():
            try:
                data = privileged.request("hotspot.start", timeout=95)
            except privileged.PrivilegedHelperError as exc:
                log.error("Privileged setup hotspot start failed: %s", exc)
                return False
            return self._command_result_ok(data, "hotspot.start")
        ok, error = self._run_system_command(
            ["systemctl", "restart", "camera-hotspot.service"],
            timeout=90,
        )
        if not ok:
            log.error("Setup hotspot restart failed: %s", error)
        return ok

    @staticmethod
    def _run_system_command(cmd: list[str], *, timeout: int) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            return False, str(exc)
        detail = (result.stderr or result.stdout or "").strip()
        if result.returncode == 0:
            return True, detail
        return False, detail or f"exit {result.returncode}"

    @staticmethod
    def _command_result_ok(data: dict, label: str) -> bool:
        returncode = int(data.get("returncode") or 0)
        if returncode == 0:
            return True
        detail = str(data.get("stderr") or data.get("stdout") or "").strip()
        log.error("%s failed: %s", label, detail or f"exit {returncode}")
        return False
