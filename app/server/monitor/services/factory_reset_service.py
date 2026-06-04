# REQ: SWR-018; RISK: RISK-006; SEC: SC-006; TEST: TC-015
"""
Factory reset service — wipes all user data and returns to first-boot state.

Single responsibility: clear configuration, certificates, recordings, and
logs. After reset, the server restarts and presents the setup wizard.

Design:
- Constructor injection (store, audit, data_dir)
- Audit log written BEFORE data is wiped (so the event is captured)
- Subprocess call for service restart (systemd)
- Does NOT reformat the /data partition - just clears contents
- Does NOT touch build-time CA trust anchors in /etc/home-monitor/trust
"""

import logging
import os
import shutil
import subprocess
import threading

from monitor.services import privileged
from monitor.services.backup_paths import build_backup_paths

log = logging.getLogger("monitor.services.factory_reset")


class FactoryResetService:
    """Wipes all user data and restarts the server in first-boot state."""

    def __init__(self, store, audit, data_dir: str = "/data"):
        self._store = store
        self._audit = audit
        self._data_dir = data_dir
        self._paths = build_backup_paths(data_dir=data_dir)

    def execute_reset(
        self,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        """Perform factory reset.

        Clears runtime config, certs, recordings, logs, and pairing state.
        Schedules a service restart after a short delay.

        Returns (message, status_code).
        """
        # Log BEFORE wiping (so the audit event is captured)
        self._log_audit(
            "FACTORY_RESET",
            requesting_user=requesting_user,
            requesting_ip=requesting_ip,
            detail="full_wipe=True",
        )

        errors = []

        # 1. Remove first-boot/setup stamps so the post-reset boot follows
        # the same path as a freshly flashed SD card.
        for stamp_name in (".setup-done", ".first-boot-done"):
            self._safe_remove(os.path.join(self._data_dir, stamp_name), errors)

        # 2. Clear all runtime config contents. The build-time provisioning CA
        # lives under /etc/home-monitor/trust and is intentionally outside
        # this wipe boundary.
        self._wipe_dir_contents_recursive(
            str(self._paths.config_dir),
            "runtime config",
            errors,
        )

        # 3. Clear mutable state directories shared with backup/import.
        for target in self._paths.resettable_dirs:
            self._safe_rmtree(str(target), errors)

        # 4. Clear WiFi credentials via hotspot script (ADR-0013)
        self._clear_wifi(errors)

        if errors:
            log.warning("Factory reset completed with errors: %s", errors)
        else:
            log.info("Factory reset completed successfully")

        # Schedule service restart (give time for HTTP response)
        self._schedule_restart()

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
        """Clear WiFi credentials via hotspot script + direct cleanup.

        The hotspot script's 'wipe' command handles nmcli deletion and
        file cleanup. We also directly clean /data/network/ as a safety
        net — nm-persist.sh bind-mounts this over /etc/NetworkManager/
        system-connections/ on every boot, so it must be wiped too.
        """
        # 1. Run hotspot script wipe (handles nmcli + /etc cleanup)
        hotspot_script = self._find_hotspot_script()
        if hotspot_script:
            try:
                result = subprocess.run(
                    [hotspot_script, "wipe"],
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
                    log.debug("WiFi credentials wiped via %s", hotspot_script)
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                log.warning("Failed to wipe WiFi credentials: %s", exc)
                errors.append(f"wifi: {exc}")
        else:
            log.warning("Hotspot script not found — skipping script wipe")

        # 2. Always wipe /data/network/system-connections/ directly
        #    (nm-persist.sh restores connections from here on every boot)
        self._wipe_dir_contents(
            str(self._paths.wifi_connections_dir),
            "persistent WiFi",
            errors,
        )

        # 3. Write a marker so nm-persist.sh skips re-seeding from rootfs
        #    (rootfs may have baked-in WiFi connections from dev builds)
        marker = str(self._paths.wifi_wiped_marker)
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w") as f:
                f.write("1\n")
            log.debug("WiFi wipe marker written: %s", marker)
        except OSError as exc:
            log.warning("Failed to write wifi wipe marker: %s", exc)
            errors.append(f"wifi-marker: {exc}")

    def _wipe_dir_contents(self, dirpath: str, label: str, errors: list):
        """Remove all files in a directory (not the directory itself)."""
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

    def _wipe_dir_contents_recursive(self, dirpath: str, label: str, errors: list):
        """Remove all files and child directories while preserving dirpath."""
        if not os.path.isdir(dirpath):
            return
        for name in os.listdir(dirpath):
            path = os.path.join(dirpath, name)
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                log.debug("Removed %s: %s", label, path)
            except OSError as exc:
                log.warning("Failed to remove %s: %s", path, exc)
                errors.append(f"{label}: {exc}")

    @staticmethod
    def _find_hotspot_script() -> str | None:
        """Locate the hotspot management script for this device."""
        candidates = [
            "/opt/monitor/scripts/monitor-hotspot.sh",
            "/opt/camera/scripts/camera-hotspot.sh",
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _schedule_restart(self):
        """Recover into first-boot setup after a 2-second delay.

        A full reboot (not just service restart) is required so that
        the monitor-hotspot.service ConditionPathExists check re-evaluates
        and starts the WiFi hotspot for first-boot setup.
        """
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
        """Request a system reboot and return whether systemd accepted it."""
        log.info("Rebooting system for factory reset...")
        if privileged.should_use_helper():
            try:
                result = privileged.request("system.reboot", timeout=20)
            except privileged.PrivilegedHelperError as exc:
                log.error("Privileged reboot request failed: %s", exc)
                return False
            return self._command_result_ok(result, "privileged reboot")

        ok, error = self._run_system_command(["systemctl", "reboot"], timeout=30)
        if not ok:
            log.error("System reboot failed: %s", error)
        return ok

    def _start_setup_hotspot(self) -> bool:
        """Restart the setup hotspot service if reboot is unavailable."""
        if privileged.should_use_helper():
            try:
                result = privileged.request("hotspot.start", timeout=90)
            except privileged.PrivilegedHelperError as exc:
                log.error("Privileged setup hotspot start failed: %s", exc)
                return False
            return self._command_result_ok(result, "privileged hotspot start")

        ok, error = self._run_system_command(
            ["systemctl", "restart", "monitor-hotspot.service"],
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
        return FactoryResetService._command_result_ok(result, " ".join(cmd)), (
            (result.stderr or result.stdout or "").strip()
        )

    @staticmethod
    def _command_result_ok(result, label: str) -> bool:
        if isinstance(result, dict):
            returncode = int(result.get("returncode", 0) or 0)
            stderr = str(result.get("stderr", "") or "").strip()
            stdout = str(result.get("stdout", "") or "").strip()
        else:
            returncode = getattr(result, "returncode", 1)
            stderr = str(getattr(result, "stderr", "") or "").strip()
            stdout = str(getattr(result, "stdout", "") or "").strip()
        if returncode == 0:
            return True
        detail = stderr or stdout or f"exit {returncode}"
        log.error("%s failed: %s", label, detail)
        return False

    def _log_audit(self, event, requesting_user="", requesting_ip="", detail=""):
        """Write audit event, fail-silent."""
        if not self._audit:
            return
        try:
            self._audit.log_event(
                event,
                user=requesting_user,
                ip=requesting_ip,
                detail=detail,
            )
        except Exception:
            pass
