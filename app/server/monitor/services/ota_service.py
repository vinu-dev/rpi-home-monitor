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
import re
import shutil
import subprocess
import threading
import uuid

from monitor import ota_policy, status_led
from monitor.services import privileged

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


# --- .swu metadata extraction ---------------------------------------------
# See app/camera/camera_streamer/ota_installer.py for the twin of this
# helper. A .swu is a CPIO newc / newc-CRC archive whose first entry
# is sw-description; we read just that entry so the admin can see the
# target version before triggering an install. Never raises — empty
# string means "unknown".
_CPIO_NEWC_MAGICS = (b"070701", b"070702")
_CPIO_HEADER_LEN = 110  # magic (6) + 13 x 8 hex fields (104)


def _read_swu_description(swu_path):
    """Return the bundle's sw-description text, or '' if unreadable."""
    try:
        with open(swu_path, "rb") as f:
            magic = f.read(6)
            if magic not in _CPIO_NEWC_MAGICS:
                return ""
            header = f.read(_CPIO_HEADER_LEN - 6)
            if len(header) != _CPIO_HEADER_LEN - 6:
                return ""
            file_size = int(header[48:56], 16)
            name_size = int(header[88:96], 16)
            name = f.read(name_size)
            pad = (4 - (_CPIO_HEADER_LEN + name_size) % 4) % 4
            f.read(pad)
            data = f.read(file_size)
            if name.rstrip(b"\0") != b"sw-description":
                return ""
            return data.decode("utf-8", "replace")
    except (OSError, ValueError, UnicodeDecodeError):
        return ""


def extract_bundle_version(swu_path):
    """Return the version string declared inside the bundle's
    sw-description, or '' if unreadable. Caller handles the empty
    case — older bundles without a version field exist in the wild
    and shouldn't block an install."""
    text = _read_swu_description(swu_path)
    m = re.search(r'version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def extract_bundle_target(swu_path):
    """Return the intended OTA target: 'server', 'camera', or ''.

    SWUpdate selects hardware sections from sw-description. We use the same
    section names to keep the server UI from presenting a server image as a
    camera image when an old or manually-copied file is left in the camera
    inbox.
    """
    text = _read_swu_description(swu_path)
    if re.search(r"\bhome-monitor-camera\s*=", text):
        return "camera"
    if re.search(r"\braspberrypi4-64\s*=", text):
        return "server"
    return ""


def sanitize_bundle_filename(filename):
    """Return a safe local filename for an uploaded .swu, or ''."""
    filename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "-", filename).strip(".-")
    if not filename or not filename.lower().endswith(".swu"):
        return ""
    return filename


class OTAService:
    """Manages OTA update verification, staging, and installation.

    Args:
        store: Store instance for settings persistence.
        audit: AuditLogger instance (optional).
        data_dir: Base data directory (default: /data).
        public_key_path: SWUpdate certificate path for bundle verification.
    """

    # REQ: SWR-010; RISK: RISK-004; SEC: SC-003; TEST: TC-009

    def __init__(
        self,
        store,
        audit=None,
        data_dir="/data",
        public_key_path=None,
        enforce_marker_path=None,
    ):
        self._store = store
        self._audit = audit
        self._data_dir = data_dir
        self._public_key_path = public_key_path or "/etc/swupdate-public.crt"
        self._enforce_marker_path = enforce_marker_path or "/etc/swupdate-enforce"
        self._status = {}
        self._status_lock = threading.Lock()

    def _set_status_led(self, state):
        try:
            if privileged.should_use_helper():
                privileged.request(
                    "led.set",
                    {"state": state, "role": "server", "force": True},
                    timeout=10,
                )
            else:
                status_led.set_state(state, role="server", force=True)
        except Exception as exc:
            log.debug("status LED update failed: %s", exc)

    def _mark_activation(self, target_version):
        try:
            status_led.mark_activation(
                "server", target_version, data_dir=self._data_dir
            )
        except Exception as exc:
            log.debug("activation marker write failed: %s", exc)

    def prepare_reboot_activation(self, target_version):
        """Mark the next boot as an OTA activation window."""
        self._mark_activation(target_version)
        self._set_status_led("ota-rebooting")

    @property
    def ota_dir(self):
        return os.path.join(self._data_dir, "ota")

    @property
    def inbox_dir(self):
        return os.path.join(self.ota_dir, "inbox")

    @property
    def staging_dir(self):
        return os.path.join(self.ota_dir, "staging")

    @property
    def swupdate_tmp_dir(self):
        return os.path.join(self.ota_dir, "tmp")

    @property
    def camera_staging_dir(self):
        return os.path.join(self.ota_dir, "camera-library")

    def _swupdate_env(self):
        os.makedirs(self.swupdate_tmp_dir, exist_ok=True)
        env = os.environ.copy()
        env["TMPDIR"] = self.swupdate_tmp_dir
        return env

    def ensure_storage(self):
        """Create and repair OTA storage directories.

        OTA storage lives under /data and must be writable by the unprivileged
        monitor process. Factory reset, older images, or a root-run recovery
        can leave /data/ota owned by root; when that happens we ask the
        allowlisted privileged helper to repair only this subtree.
        """
        err = self._create_storage_dirs()
        probe_err = self._probe_storage_writable()
        if not err and not probe_err:
            return True, ""

        if privileged.should_use_helper():
            try:
                privileged.request("ota.repair_storage", timeout=20)
            except privileged.PrivilegedHelperError as exc:
                details = probe_err or err or str(exc)
                return (
                    False,
                    f"OTA storage is not writable and repair failed: {details}",
                )
            err = self._create_storage_dirs()
            probe_err = self._probe_storage_writable()
            if not err and not probe_err:
                return True, ""

        return False, f"OTA storage is not writable: {probe_err or err}"

    def _create_storage_dirs(self):
        try:
            os.makedirs(self.inbox_dir, exist_ok=True)
            os.makedirs(self.staging_dir, exist_ok=True)
            os.makedirs(self.swupdate_tmp_dir, exist_ok=True)
            os.makedirs(self.camera_staging_dir, exist_ok=True)
            return ""
        except OSError as exc:
            return str(exc)

    def _probe_storage_writable(self):
        for directory in (
            self.inbox_dir,
            self.staging_dir,
            self.swupdate_tmp_dir,
            self.camera_staging_dir,
        ):
            err = self._probe_directory_writable(directory)
            if err:
                return err
        return ""

    def _probe_directory_writable(self, directory):
        probe = os.path.join(directory, f".write-test-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            os.unlink(probe)
            return ""
        except OSError as exc:
            try:
                if os.path.exists(probe):
                    os.unlink(probe)
            except OSError:
                pass
            return f"{directory}: {exc}"

    def get_status(self, device_id="server", current_version=""):
        """Get update status for a device.

        The in-memory status dict is transient — it vanishes on restart.
        If we have no in-RAM status for the server but a .swu is sitting
        in the staging dir (from a prior upload that survived the
        process lifecycle), reconstruct a "staged" state from disk so
        the UI keeps showing the Install button.
        """
        with self._status_lock:
            status = self._status.get(device_id)
            status_copy = dict(status) if status is not None else None
        if status_copy is not None:
            if device_id == "server":
                return self._apply_staged_policy(status_copy, current_version)
            return status_copy

        default = {"state": "idle", "version": "", "progress": 0, "error": ""}
        if device_id == "server":
            staged = self._find_staged_bundle()
            if staged:
                staged_path = os.path.join(self.staging_dir, staged)
                target_version = extract_bundle_version(staged_path)
                decision = ota_policy.classify_update(current_version, target_version)
                if decision.blocked or decision.relation == "same":
                    reason = decision.reason or "Discarded already-installed update."
                    self._discard_staged_bundle(staged, reason)
                    default["error"] = decision.reason
                    return default
                default["state"] = "staged"
                default["staged_filename"] = staged
                default["target_version"] = target_version
                default["update_relation"] = decision.relation
        return default

    def is_busy(self, device_id="server"):
        """True iff an upload or install for this device is in flight.

        Mirrors the camera's ota_installer.is_busy(). Used by the
        upload and install endpoints to reject concurrent admin
        actions with HTTP 409 instead of silently clobbering state.
        """
        state = self.get_status(device_id).get("state", "idle")
        return state in ("uploading", "verifying", "installing", "rebooting")

    def get_verification_posture(self):
        """Return operator-visible OTA signature verification posture."""
        public_key_present = os.path.isfile(self._public_key_path)
        enforcement_marker_present = os.path.isfile(self._enforce_marker_path)
        swupdate_available = shutil.which("swupdate") is not None
        verification_active = public_key_present and swupdate_available
        install_blocked = enforcement_marker_present and not verification_active
        allows_unsigned_fallback = (not enforcement_marker_present) and (
            not verification_active
        )

        if install_blocked:
            mode = "blocked"
            warning = (
                "OTA signature enforcement is enabled, but SWUpdate verification "
                "is unavailable. Installs are blocked until the verifier and "
                "public certificate are restored."
            )
        elif verification_active and enforcement_marker_present:
            mode = "enforced"
            warning = ""
        elif verification_active:
            mode = "verified"
            warning = (
                "OTA bundles are signature-checked, but this image is missing "
                "the production enforcement marker."
            )
        else:
            mode = "dev-fallback"
            warning = (
                "OTA signature verification is unavailable on this device. "
                "This is a development fallback; unsigned bundles may be accepted."
            )

        return {
            "mode": mode,
            "verification_active": verification_active,
            "verification_enforced": enforcement_marker_present,
            "public_key_present": public_key_present,
            "swupdate_available": swupdate_available,
            "install_blocked": install_blocked,
            "allows_unsigned_fallback": allows_unsigned_fallback,
            "warning": warning,
        }

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

    def _apply_staged_policy(self, status, current_version=""):
        """Apply persisted-staging safety to an in-memory status snapshot."""
        if status.get("state") != "staged":
            return status
        filename = status.get("staged_filename") or self._find_staged_bundle()
        if not filename:
            return status
        path = os.path.join(self.staging_dir, filename)
        target_version = status.get("target_version") or extract_bundle_version(path)
        decision = ota_policy.classify_update(current_version, target_version)
        if decision.blocked or decision.relation == "same":
            reason = decision.reason or "Discarded already-installed update."
            self._discard_staged_bundle(filename, reason)
            self.set_status("server", "idle", error=decision.reason)
            return {
                "state": "idle",
                "version": "",
                "progress": 0,
                "error": decision.reason,
            }
        status["target_version"] = target_version
        status["update_relation"] = decision.relation
        return status

    def _discard_staged_bundle(self, filename, reason):
        """Remove one staged server bundle that failed OTA policy."""
        try:
            os.unlink(os.path.join(self.staging_dir, filename))
            log.warning("Discarded staged OTA bundle %s: %s", filename, reason)
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("Failed to discard staged OTA bundle %s: %s", filename, exc)

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

    def stage_bundle(self, source_path, filename, user="", ip="", current_version=""):
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

        ok, storage_err = self.ensure_storage()
        if not ok:
            return None, storage_err

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

        target_version = extract_bundle_version(source_path)
        decision = ota_policy.classify_update(current_version, target_version)
        if decision.blocked:
            return None, decision.reason

        # Create staging directory
        os.makedirs(self.staging_dir, exist_ok=True)
        staged_path = os.path.join(self.staging_dir, filename)

        # os.replace is atomic on POSIX within a single filesystem and
        # atomically overwrites an existing destination; shutil.move falls
        # back to copy+delete when source and destination live on different
        # mounts. Two concurrent uploads against the same filename would
        # happily interleave copy+delete and land a corrupted bundle in
        # staging. Stage uploads to a per-request temp file under the same
        # directory, then os.replace() into place.
        tmp_path = f"{staged_path}.partial-{os.getpid()}-{os.urandom(4).hex()}"
        try:
            shutil.move(source_path, tmp_path)
            os.replace(tmp_path, staged_path)
        except OSError as e:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            return None, f"Failed to stage file: {e}"

        self.set_status(
            "server",
            "staged",
            version="",
            progress=0,
            error="",
            staged_filename=filename,
            target_version=target_version,
            update_relation=decision.relation,
        )
        self._log_audit(
            "OTA_STAGED",
            user,
            ip,
            f"Bundle staged: {filename} (version={target_version or 'unknown'})",
        )
        log.info(
            "OTA bundle staged: %s (%d bytes, version=%s)",
            filename,
            size,
            target_version or "unknown",
        )

        return staged_path, ""

    def verify_bundle(self, bundle_path):
        """Verify CMS signature of a .swu bundle.

        Uses swupdate to verify the signature embedded in the .swu.

        Enforcement contract (per ADR-0014):
          - If the image was built with SWUPDATE_SIGNING=1, the swupdate
            bbappend drops `/etc/swupdate-enforce` onto the rootfs as a
            marker. In that case a missing public cert is a HARD FAIL —
            we will not install unsigned bundles on a device where the
            user opted into signing.
          - If the marker is absent, a missing cert means "dev build,
            signing was never required" and we accept any bundle.

        Args:
            bundle_path: Path to the .swu file.

        Returns:
            (valid, error) tuple.
        """
        if not os.path.isfile(bundle_path):
            return False, "Bundle file not found"

        if not os.path.isfile(self._public_key_path):
            if os.path.isfile(self._enforce_marker_path):
                log.error(
                    "Signing enforced but cert missing at %s — refusing install",
                    self._public_key_path,
                )
                return False, (
                    "Signature enforcement is on but the verification "
                    "certificate is missing from this device. Re-flash "
                    "an image rebuilt with your current signing key."
                )
            log.warning(
                "SWUpdate verification cert not found at %s — skipping verification (dev build)",
                self._public_key_path,
            )
            return True, ""  # No key + no enforcement = dev mode

        try:
            if privileged.should_use_helper():
                result_data = privileged.request(
                    "ota.verify",
                    {
                        "bundle_path": bundle_path,
                        "public_key_path": self._public_key_path,
                    },
                    timeout=60,
                )
                returncode = int(result_data.get("returncode") or 0)
                stderr = str(result_data.get("stderr") or "")
            else:
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
                    env=self._swupdate_env(),
                )
                returncode = result.returncode
                stderr = result.stderr
            if returncode == 0:
                log.info("Bundle signature verified: %s", bundle_path)
                return True, ""
            else:
                error = stderr.strip() or "Signature verification failed"
                log.error("Bundle verification failed: %s", error)
                return False, error

        except FileNotFoundError:
            if os.path.isfile(self._enforce_marker_path):
                log.error("Signing enforced but swupdate is not installed")
                return False, (
                    "Signature enforcement is on but swupdate is not installed. "
                    "Repair the OTA verifier before installing updates."
                )
            log.warning("swupdate not found — skipping verification")
            return True, ""  # swupdate not installed (dev/test)
        except subprocess.TimeoutExpired:
            return False, "Verification timed out"
        except privileged.PrivilegedHelperError as e:
            return False, str(e)
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

        self._set_status_led("ota-installing")
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
            if privileged.should_use_helper():
                data = privileged.request(
                    "ota.install",
                    {
                        "bundle_path": bundle_path,
                        "public_key_path": (
                            self._public_key_path
                            if os.path.isfile(self._public_key_path)
                            else ""
                        ),
                    },
                    timeout=600,
                )
                rc = int(data.get("returncode") or 0)
                stderr = str(data.get("stderr") or "")
            else:
                proc = subprocess.Popen(
                    self._install_command(bundle_path),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=self._swupdate_env(),
                )
                try:
                    _stdout, stderr = proc.communicate(timeout=600)
                    rc = proc.returncode
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    err = "Installation timed out (10 min)"
                    self.set_status("server", "error", error=err)
                    self._set_status_led("error")
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
            self._set_status_led("error")
            self._log_audit("OTA_INSTALL_FAILED", user, ip, f"Install failed: {error}")
            return False, error

        except FileNotFoundError:
            err = "swupdate not installed"
            self.set_status("server", "error", error=err)
            self._set_status_led("error")
            return False, err
        except privileged.PrivilegedHelperError as e:
            self.set_status("server", "error", error=str(e))
            self._set_status_led("error")
            return False, str(e)
        except OSError as e:
            self.set_status("server", "error", error=str(e))
            self._set_status_led("error")
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

    def schedule_reboot(self, delay_seconds=2.0):
        """Schedule a system reboot after `delay_seconds`.

        Runs on a daemon thread so the HTTP handler can flush its response
        before systemd tears down the Flask worker.
        """

        def _reboot_after_delay():
            import time as _t

            _t.sleep(delay_seconds)
            try:
                if privileged.should_use_helper():
                    privileged.request("system.reboot", timeout=15)
                else:
                    subprocess.run(["reboot"], check=False, timeout=15)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.error("reboot command failed: %s", exc)
            except privileged.PrivilegedHelperError as exc:
                log.error("reboot helper command failed: %s", exc)

        threading.Thread(
            target=_reboot_after_delay,
            daemon=True,
            name="ota-install-reboot",
        ).start()

    def _log_audit(self, event, user, ip, detail):
        """Log audit event (fail-silent)."""
        if self._audit:
            try:
                self._audit.log_event(event, user=user, ip=ip, detail=detail)
            except Exception:
                pass
