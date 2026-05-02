# REQ: SWR-018; RISK: RISK-006; SEC: SC-006; TEST: TC-015
"""
Factory reset service — wipes all user data and returns to first-boot state.

Single responsibility: clear configuration, certificates, recordings, and
logs. After reset, the server restarts and presents the setup wizard.

Design:
- Constructor injection (store, audit, data_dir)
- Audit log written BEFORE data is wiped (so the event is captured)
- Subprocess call for service restart (systemd)
- Does NOT reformat the /data partition — just clears contents
"""

import logging
import os
import shutil
import subprocess
import threading

log = logging.getLogger("monitor.services.factory_reset")


class FactoryResetService:
    """Wipes all user data and restarts the server in first-boot state."""

    def __init__(self, store, audit, data_dir: str = "/data"):
        self._store = store
        self._audit = audit
        self._data_dir = data_dir

    def execute_reset(
        self,
        keep_recordings: bool = False,
        requesting_user: str = "",
        requesting_ip: str = "",
    ) -> tuple[str, int]:
        """Perform factory reset.

        Clears all config, certs, and optionally recordings.
        Schedules a service restart after a short delay.

        Returns (message, status_code).
        """
        # Log BEFORE wiping (so the audit event is captured)
        self._log_audit(
            "FACTORY_RESET",
            requesting_user=requesting_user,
            requesting_ip=requesting_ip,
            detail=f"keep_recordings={keep_recordings}",
        )

        errors = []

        # 1. Remove setup-done stamp (re-enables provisioning wizard)
        stamp = os.path.join(self._data_dir, ".setup-done")
        self._safe_remove(stamp, errors)

        # 2. Clear config files (users, cameras, settings, secret key)
        config_dir = os.path.join(self._data_dir, "config")
        for filename in [
            "cameras.json",
            "users.json",
            "settings.json",
            ".secret_key",
        ]:
            self._safe_remove(os.path.join(config_dir, filename), errors)

        # 3. Clear certificates (server certs regenerated on boot)
        certs_dir = os.path.join(self._data_dir, "certs")
        self._safe_rmtree(certs_dir, errors)

        # 4. Clear live streaming buffer
        live_dir = os.path.join(self._data_dir, "live")
        self._safe_rmtree(live_dir, errors)

        # 5. Optionally clear recordings
        if not keep_recordings:
            recordings_dir = os.path.join(self._data_dir, "recordings")
            self._safe_rmtree(recordings_dir, errors)

        # 6. Clear logs (audit log already has the reset event)
        logs_dir = os.path.join(self._data_dir, "logs")
        self._safe_rmtree(logs_dir, errors)

        # 7. Clear Tailscale state
        ts_dir = os.path.join(self._data_dir, "tailscale")
        self._safe_rmtree(ts_dir, errors)

        # 8. Clear OTA staging area
        ota_dir = os.path.join(self._data_dir, "ota")
        self._safe_rmtree(ota_dir, errors)

        # 9. Clear WiFi credentials via hotspot script (ADR-0013)
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
        persist_dir = os.path.join(self._data_dir, "network", "system-connections")
        self._wipe_dir_contents(persist_dir, "persistent WiFi", errors)

        # 3. Write a marker so nm-persist.sh skips re-seeding from rootfs
        #    (rootfs may have baked-in WiFi connections from dev builds)
        marker = os.path.join(self._data_dir, "network", ".wifi-wiped")
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
        """Reboot the system after a 2-second delay.

        A full reboot (not just service restart) is required so that
        the monitor-hotspot.service ConditionPathExists check re-evaluates
        and starts the WiFi hotspot for first-boot setup.
        """

        def _do_restart():
            log.info("Rebooting system for factory reset...")
            try:
                subprocess.run(
                    ["systemctl", "reboot"],
                    capture_output=True,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                log.error("System reboot failed: %s", exc)

        timer = threading.Timer(2.0, _do_restart)
        timer.daemon = True
        timer.start()

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
