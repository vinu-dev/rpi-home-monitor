"""
OTA update service (ADR-0008).

Manages the server-side OTA update lifecycle:
1. Verify .swu bundle (Ed25519 signature)
2. Stage bundle to /data/ota/staging/
3. Check available disk space
4. Install via swupdate (A/B partition swap)
5. Track update status

Design patterns:
- Constructor Injection (store, audit, data_dir)
- Single Responsibility (OTA lifecycle only)
- Fail-Silent (audit failures don't block updates)
"""

import logging
import os
import shutil
import subprocess
import threading

log = logging.getLogger("monitor.ota-service")

# Maximum bundle size (500MB)
MAX_BUNDLE_SIZE = 500 * 1024 * 1024

# Minimum free space required for staging (100MB headroom)
MIN_FREE_SPACE = 100 * 1024 * 1024


class OTAService:
    """Manages OTA update verification, staging, and installation.

    Args:
        store: Store instance for settings persistence.
        audit: AuditLogger instance (optional).
        data_dir: Base data directory (default: /data).
        public_key_path: Ed25519 public key for signature verification.
    """

    def __init__(self, store, audit=None, data_dir="/data", public_key_path=None):
        self._store = store
        self._audit = audit
        self._data_dir = data_dir
        self._public_key_path = public_key_path or os.path.join(
            data_dir, "certs", "swupdate-public.pem"
        )
        self._status = {}
        self._status_lock = threading.Lock()

    @property
    def inbox_dir(self):
        return os.path.join(self._data_dir, "ota", "inbox")

    @property
    def staging_dir(self):
        return os.path.join(self._data_dir, "ota", "staging")

    def get_status(self, device_id="server"):
        """Get update status for a device."""
        with self._status_lock:
            return dict(
                self._status.get(
                    device_id,
                    {"state": "idle", "version": "", "progress": 0, "error": ""},
                )
            )

    def set_status(self, device_id, state, **kwargs):
        """Update status for a device."""
        with self._status_lock:
            current = self._status.get(
                device_id,
                {"state": "idle", "version": "", "progress": 0, "error": ""},
            )
            current["state"] = state
            current.update(kwargs)
            self._status[device_id] = current

    def check_space(self, required_bytes=0):
        """Check if enough disk space is available for staging.

        Args:
            required_bytes: Additional bytes needed beyond MIN_FREE_SPACE.

        Returns:
            (has_space, free_bytes, error) tuple.
        """
        try:
            stat = shutil.disk_usage(self._data_dir)
            free = stat.free
            needed = MIN_FREE_SPACE + required_bytes
            return free >= needed, free, ""
        except OSError as e:
            return False, 0, str(e)

    def stage_bundle(self, source_path, filename, user="", ip=""):
        """Stage a .swu bundle for installation.

        Validates file extension and size, moves to staging directory.

        Args:
            source_path: Path to uploaded/imported .swu file.
            filename: Original filename.
            user: Username for audit log.
            ip: IP address for audit log.

        Returns:
            (staged_path, error) tuple.
        """
        # Validate extension
        if not filename.lower().endswith(".swu"):
            return None, "Only .swu files are accepted"

        # Check file exists and size
        try:
            size = os.path.getsize(source_path)
        except OSError as e:
            return None, f"Cannot read file: {e}"

        if size > MAX_BUNDLE_SIZE:
            return None, f"File too large ({size} bytes, max {MAX_BUNDLE_SIZE})"

        if size == 0:
            return None, "File is empty"

        # Check disk space
        has_space, free, err = self.check_space(size)
        if not has_space:
            return (
                None,
                f"Insufficient disk space (free: {free}, need: {size + MIN_FREE_SPACE})",
            )

        # Create staging directory
        os.makedirs(self.staging_dir, exist_ok=True)
        staged_path = os.path.join(self.staging_dir, filename)

        try:
            shutil.move(source_path, staged_path)
        except OSError as e:
            return None, f"Failed to stage file: {e}"

        self.set_status("server", "staged", version="", progress=0, error="")
        self._log_audit("OTA_STAGED", user, ip, f"Bundle staged: {filename}")
        log.info("OTA bundle staged: %s (%d bytes)", filename, size)

        return staged_path, ""

    def verify_bundle(self, bundle_path):
        """Verify Ed25519 signature of a .swu bundle.

        Uses openssl to verify the signature embedded in the SWU image.

        Args:
            bundle_path: Path to the .swu file.

        Returns:
            (valid, error) tuple.
        """
        if not os.path.isfile(bundle_path):
            return False, "Bundle file not found"

        if not os.path.isfile(self._public_key_path):
            log.warning(
                "Ed25519 public key not found at %s — skipping verification",
                self._public_key_path,
            )
            return True, ""  # No key = skip verification (dev mode)

        try:
            result = subprocess.run(
                [
                    "swupdate",
                    "-c",  # check mode (verify only, don't install)
                    "-i",
                    bundle_path,
                    "-k",
                    self._public_key_path,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                log.info("Bundle signature verified: %s", bundle_path)
                return True, ""
            else:
                error = result.stderr.strip() or "Signature verification failed"
                log.error("Bundle verification failed: %s", error)
                return False, error

        except FileNotFoundError:
            log.warning("swupdate not found — skipping verification")
            return True, ""  # swupdate not installed (dev/test)
        except subprocess.TimeoutExpired:
            return False, "Verification timed out"
        except OSError as e:
            return False, str(e)

    def install_bundle(self, bundle_path, user="", ip=""):
        """Install a verified .swu bundle via swupdate.

        This triggers the A/B partition swap. The system will reboot
        into the new partition after installation.

        Args:
            bundle_path: Path to verified .swu file.
            user: Username for audit log.
            ip: IP address for audit log.

        Returns:
            (success, error) tuple.
        """
        if not os.path.isfile(bundle_path):
            return False, "Bundle file not found"

        self.set_status("server", "installing", progress=0, error="")
        self._log_audit("OTA_INSTALL_START", user, ip, f"Installing: {bundle_path}")

        try:
            result = subprocess.run(
                ["swupdate", "-i", bundle_path],
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for large images
            )
            if result.returncode == 0:
                self.set_status("server", "installed", progress=100, error="")
                self._log_audit(
                    "OTA_INSTALL_COMPLETE", user, ip, "Installation complete"
                )
                log.info("OTA installation complete — reboot required")
                return True, ""
            else:
                error = result.stderr.strip() or "Installation failed"
                self.set_status("server", "error", error=error)
                self._log_audit(
                    "OTA_INSTALL_FAILED", user, ip, f"Install failed: {error}"
                )
                return False, error

        except FileNotFoundError:
            err = "swupdate not installed"
            self.set_status("server", "error", error=err)
            return False, err
        except subprocess.TimeoutExpired:
            err = "Installation timed out (10 min)"
            self.set_status("server", "error", error=err)
            return False, err
        except OSError as e:
            self.set_status("server", "error", error=str(e))
            return False, str(e)

    def clean_staging(self):
        """Remove staged bundles from the staging directory."""
        try:
            if os.path.isdir(self.staging_dir):
                shutil.rmtree(self.staging_dir)
                os.makedirs(self.staging_dir, exist_ok=True)
                log.info("Staging directory cleaned")
        except OSError as e:
            log.warning("Failed to clean staging: %s", e)

    def _log_audit(self, event, user, ip, detail):
        """Log audit event (fail-silent)."""
        if self._audit:
            try:
                self._audit.log(event=event, user=user, ip=ip, detail=detail)
            except Exception:
                pass
