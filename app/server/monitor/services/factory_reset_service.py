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

    def _schedule_restart(self):
        """Restart the monitor service after a 2-second delay."""

        def _do_restart():
            log.info("Restarting monitor service for factory reset...")
            try:
                subprocess.run(
                    ["systemctl", "restart", "monitor"],
                    capture_output=True,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                log.error("Service restart failed: %s", exc)

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
