"""
OTA update service (ADR-0008).

Manages the server-side OTA update lifecycle:
1. Verify .swu bundle (CMS signature via SWUpdate)
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


def _human_size(nbytes):
    """Convert bytes to human-readable size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"


class OTAService:
    """Manages OTA update verification, staging, and installation.

    Args:
        store: Store instance for settings persistence.
        audit: AuditLogger instance (optional).
        data_dir: Base data directory (default: /data).
        public_key_path: SWUpdate certificate path for bundle verification.
    """

    def __init__(self, store, audit=None, data_dir="/data", public_key_path=None):
        self._store = store
        self._audit = audit
        self._data_dir = data_dir
        self._public_key_path = public_key_path or "/etc/swupdate-public.crt"
        self._status = {}
        self._status_lock = threading.Lock()

    @property
    def inbox_dir(self):
        return os.path.join(self._data_dir, "ota", "inbox")

    @property
    def staging_dir(self):
        return os.path.join(self._data_dir, "ota", "staging")

    def get_status(self, device_id="server"):
        """Get update status for a device.

        The in-memory status dict is transient — it vanishes on restart.
        If we have no in-RAM status for the server but a .swu is sitting
        in the staging dir (from a prior upload that survived the
        process lifecycle), reconstruct a "staged" state from disk so
        the UI keeps showing the Install button.
        """
        with self._status_lock:
            status = self._status.get(device_id)
            if status is not None:
                return dict(status)

        default = {"state": "idle", "version": "", "progress": 0, "error": ""}
        if device_id == "server":
            staged = self._find_staged_bundle()
            if staged:
                default["state"] = "staged"
                default["staged_filename"] = staged
        return default

    def _find_staged_bundle(self):
        """Return the filename of the newest staged .swu, or '' if none."""
        try:
            entries = [
                (os.path.getmtime(os.path.join(self.staging_dir, f)), f)
                for f in os.listdir(self.staging_dir)
                if f.endswith(".swu")
            ]
        except OSError:
            return ""
        if not entries:
            return ""
        entries.sort(reverse=True)
        return entries[0][1]

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
        """Verify CMS signature of a .swu bundle.

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
                "SWUpdate verification cert not found at %s — skipping verification",
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

    def _install_command(self, bundle_path):
        """Build the swupdate install command for the current environment."""
        cmd = ["swupdate", "-i", bundle_path]
        if os.path.isfile(self._public_key_path):
            cmd.extend(["-k", self._public_key_path])
        return cmd

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

        self.set_status("server", "installing", progress=5, error="")
        self._log_audit("OTA_INSTALL_START", user, ip, f"Installing: {bundle_path}")

        # Launch swupdate via Popen so we can tick a coarse progress bar
        # while it runs. The subprocess doesn't expose structured progress
        # over stdout (it writes verbose TRACE lines), but a rising
        # counter is enough for the UI to prove the server hasn't hung.
        stop_ticker = threading.Event()

        def _ticker():
            pct = 10
            while not stop_ticker.wait(3):
                pct = min(pct + 3, 90)
                self.set_status("server", "installing", progress=pct, error="")

        t = threading.Thread(target=_ticker, daemon=True, name="ota-install-ticker")
        t.start()
        try:
            proc = subprocess.Popen(
                self._install_command(bundle_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                _stdout, stderr = proc.communicate(timeout=600)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                err = "Installation timed out (10 min)"
                self.set_status("server", "error", error=err)
                return False, err

            if rc == 0:
                self.set_status("server", "installed", progress=100, error="")
                self._log_audit(
                    "OTA_INSTALL_COMPLETE", user, ip, "Installation complete"
                )
                log.info("OTA installation complete — reboot required")
                return True, ""
            error = (stderr or "").strip() or "Installation failed"
            self.set_status("server", "error", error=error)
            self._log_audit("OTA_INSTALL_FAILED", user, ip, f"Install failed: {error}")
            return False, error

        except FileNotFoundError:
            err = "swupdate not installed"
            self.set_status("server", "error", error=err)
            return False, err
        except OSError as e:
            self.set_status("server", "error", error=str(e))
            return False, str(e)
        finally:
            stop_ticker.set()
            t.join(timeout=2)

    def scan_usb(self):
        """Scan USB devices for .swu update bundles.

        Looks at all mounted USB devices for .swu files in root and
        common update directories (updates/, ota/).

        Returns:
            list of dicts: [{filename, path, size, size_human, device}]
        """
        from monitor.services import usb

        bundles = []
        try:
            devices = usb.detect_devices()
        except Exception as e:
            log.warning("USB detection failed during OTA scan: %s", e)
            return bundles

        for dev in devices:
            mp = dev.get("mountpoint", "")
            if not mp:
                continue

            # Search root and common update directories
            search_dirs = [mp]
            for subdir in ("updates", "ota", "OTA"):
                candidate = os.path.join(mp, subdir)
                if os.path.isdir(candidate):
                    search_dirs.append(candidate)

            for search_dir in search_dirs:
                try:
                    for entry in os.scandir(search_dir):
                        if entry.is_file() and entry.name.lower().endswith(".swu"):
                            stat = entry.stat()
                            bundles.append(
                                {
                                    "filename": entry.name,
                                    "path": entry.path,
                                    "size": stat.st_size,
                                    "size_human": _human_size(stat.st_size),
                                    "device": dev.get("path", ""),
                                }
                            )
                except OSError as e:
                    log.debug("Cannot read %s: %s", search_dir, e)

        log.info("USB scan found %d bundle(s)", len(bundles))
        return bundles

    def import_from_usb(self, usb_path, user="", ip=""):
        """Import a .swu bundle from a USB device.

        Copies (not moves) the file from USB to inbox, then stages it.
        The original file on USB is preserved.

        Args:
            usb_path: Full path to the .swu file on USB.
            user: Username for audit log.
            ip: IP address for audit log.

        Returns:
            (staged_path, error) tuple.
        """
        filename = os.path.basename(usb_path)

        if not filename.lower().endswith(".swu"):
            return None, "Only .swu files are accepted"

        if not os.path.isfile(usb_path):
            return None, f"File not found: {usb_path}"

        try:
            size = os.path.getsize(usb_path)
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

        # Copy to inbox (preserve original on USB)
        os.makedirs(self.inbox_dir, exist_ok=True)
        inbox_path = os.path.join(self.inbox_dir, filename)

        try:
            shutil.copy2(usb_path, inbox_path)
        except OSError as e:
            return None, f"Failed to copy from USB: {e}"

        # Stage the bundle
        staged_path, stage_err = self.stage_bundle(
            inbox_path, filename, user=user, ip=ip
        )
        if stage_err:
            try:
                os.unlink(inbox_path)
            except OSError:
                pass
            return None, stage_err

        self._log_audit("OTA_USB_IMPORT", user, ip, f"Imported from USB: {usb_path}")
        log.info("OTA bundle imported from USB: %s", usb_path)
        return staged_path, ""

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
                self._audit.log_event(event, user=user, ip=ip, detail=detail)
            except Exception:
                pass
