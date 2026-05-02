# REQ: SWR-038, SWR-010; RISK: RISK-004, RISK-019; SEC: SC-003, SC-018; TEST: TC-036, TC-013
"""
OTA install dispatcher (client half of the trigger-file protocol).

The unprivileged camera-streamer process cannot run `swupdate -i`
directly — NoNewPrivileges=true in its service unit blocks sudo,
and swupdate needs root for /dev symlinks, ext4 mount of the
standby slot, and fw_setenv.

This module implements the client half of the split:

    camera-streamer  ──write bundle──▶  /var/lib/camera-ota/staging/update.swu
                    ──write trigger──▶  /var/lib/camera-ota/trigger
                                            │
                             (systemd .path unit fires)
                                            ▼
                                camera-ota-installer.service (root)
                                            │
                                            ▼
                                swupdate -c -i / swupdate -i
                                            │
                    ◀── poll status.json ── /var/lib/camera-ota/status.json

Both OTA entry points (server-push via OTAAgent on :8080 and camera
GUI upload on :443) stage the bundle and trigger through this module
so install behaviour is identical.

Design patterns:
- Stream-to-Disk (bundle never buffered in RAM)
- File-based IPC (no D-Bus, no polkit — path unit does activation)
- Fail-Graceful (returns error, does not raise)
"""

import json
import logging
import os
import re
import tempfile
import time

log = logging.getLogger("camera-streamer.ota-installer")

SPOOL_DIR = "/var/lib/camera-ota"
STAGING_DIR = os.path.join(SPOOL_DIR, "staging")
TRIGGER_PATH = os.path.join(SPOOL_DIR, "trigger")
REBOOT_TRIGGER_PATH = os.path.join(SPOOL_DIR, "reboot-trigger")
STATUS_PATH = os.path.join(SPOOL_DIR, "status.json")
BUNDLE_NAME = "update.swu"

# States reported in status.json. Kept as module constants so tests
# and UI render logic can import them without magic strings.
STATE_IDLE = "idle"
STATE_DOWNLOADING = "downloading"
STATE_VERIFYING = "verifying"
STATE_INSTALLING = "installing"
STATE_INSTALLED = "installed"
STATE_ERROR = "error"

# Bump this when the client↔camera OTA wire contract changes in a way
# that a mismatched server's push_bundle would get wrong (e.g. the
# 202-Accepted + poll pattern we shipped in v2 replacing the blocking
# 200-on-completion in v1). The server logs a warning on mismatch but
# does not refuse — we want best-effort cross-version operation so a
# field deployment can be rolled forward without in-lockstep releases.
OTA_PROTOCOL_VERSION = 2

# Bounded wait for the root installer to report back after we trigger.
# The actual install can take several minutes; this is just the deadline
# for the state machine to *start* moving (i.e. path unit fires and
# installer writes its first status transition). If it doesn't move
# within this window something is wrong — probably the .path unit
# isn't enabled or spool dir permissions are broken.
TRIGGER_START_TIMEOUT = 30  # seconds


def bundle_path():
    """Canonical absolute path of the staged bundle."""
    return os.path.join(STAGING_DIR, BUNDLE_NAME)


# --- .swu metadata extraction ----------------------------------------------
# A .swu is a CPIO newc archive whose first entry is sw-description (a
# libconfig-ish manifest). We read just the first entry without dragging
# in the cpio binary or full extraction, so the admin can see the target
# version ("installing 1.1.0 on top of 1.0.0") before committing.

# CPIO newc ("070701") and newc-CRC ("070702") share the same header
# layout — only the c_check field differs. The .swu build script
# (scripts/build-swu.sh) uses newc-CRC, so we accept both.
_CPIO_NEWC_MAGICS = (b"070701", b"070702")
_CPIO_HEADER_LEN = 110  # magic (6) + 13 x 8 hex fields (104)


def read_first_cpio_entry(swu_path):
    """Return (name, data) of the first CPIO entry in a .swu bundle.

    Returns (None, None) if the file isn't a CPIO newc archive or the
    first entry can't be read. Never raises — OTA code paths tolerate
    "unknown version" gracefully by falling back to "--".
    """
    try:
        with open(swu_path, "rb") as f:
            magic = f.read(6)
            if magic not in _CPIO_NEWC_MAGICS:
                return None, None
            header = f.read(_CPIO_HEADER_LEN - 6)
            if len(header) != _CPIO_HEADER_LEN - 6:
                return None, None
            # CPIO newc header layout (after the 6-byte magic):
            #   c_ino(8) c_mode(8) c_uid(8) c_gid(8) c_nlink(8)
            #   c_mtime(8) c_filesize(8) c_devmajor(8) c_devminor(8)
            #   c_rdevmajor(8) c_rdevminor(8) c_namesize(8) c_check(8)
            # → filesize starts at original offset 54 (= header[48:56]),
            #   namesize starts at original offset 94 (= header[88:96]).
            file_size = int(header[48:56], 16)
            name_size = int(header[88:96], 16)
            name = f.read(name_size)
            # Pad (header + filename) to 4-byte boundary.
            pad = (4 - (_CPIO_HEADER_LEN + name_size) % 4) % 4
            f.read(pad)
            data = f.read(file_size)
            if len(data) != file_size:
                return None, None
            return name.rstrip(b"\0").decode("utf-8", "replace"), data
    except (OSError, ValueError):
        return None, None


def extract_bundle_version(swu_path):
    """Return the ``version`` field from a .swu's sw-description, or
    empty string if the bundle isn't readable or doesn't carry a
    version. Callers should treat empty as "unknown" — never block an
    install on it (older bundles exist in the wild)."""
    name, data = read_first_cpio_entry(swu_path)
    if name != "sw-description" or not data:
        return ""
    try:
        text = data.decode("utf-8", "replace")
    except UnicodeDecodeError:
        return ""
    # sw-description is libconfig syntax. We only care about:
    #   version = "dev-20260418-1957";
    m = re.search(r'version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else ""


def read_status():
    """Read the installer's status JSON. Returns a dict with defaults
    if the file is missing/unreadable.
    """
    try:
        with open(STATUS_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("status.json is not an object")
        data.setdefault("state", STATE_IDLE)
        data.setdefault("progress", 0)
        data.setdefault("error", "")
        data["protocol_version"] = OTA_PROTOCOL_VERSION
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "state": STATE_IDLE,
            "progress": 0,
            "error": "",
            "protocol_version": OTA_PROTOCOL_VERSION,
        }


def write_status(state, progress=0, error="", **extra):
    """Write a status transition from the client side (e.g. during the
    'downloading' phase, before the root installer takes over).

    Writes atomically via rename so readers never see a half-written
    JSON document. Extra keyword args (e.g. ``target_version``) are
    merged into the payload so the UI can read them verbatim.
    """
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
    except OSError:
        pass
    payload = {
        "state": state,
        "progress": int(progress),
        "error": error or "",
        "updated_at": int(time.time()),
    }
    payload.update(extra)
    data = json.dumps(payload).encode()
    try:
        fd, tmp = tempfile.mkstemp(prefix=".status.", suffix=".json", dir=SPOOL_DIR)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o664)
            os.replace(tmp, STATUS_PATH)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        log.warning("Could not write status.json: %s", exc)


def is_busy():
    """True if an install is in progress (trigger present or installer
    hasn't reached a terminal state). Used to reject concurrent uploads.
    """
    if os.path.exists(TRIGGER_PATH):
        return True
    state = read_status().get("state", STATE_IDLE)
    return state in (STATE_VERIFYING, STATE_INSTALLING, STATE_DOWNLOADING)


def stage_bundle(src_fileobj, total_bytes, progress_cb=None):
    """Stream an uploaded bundle into the spool.

    Args:
        src_fileobj: readable file-like object (e.g. HTTP handler.rfile).
        total_bytes: declared Content-Length. Validated against received.
        progress_cb: optional callable(received, total) for UI progress.

    Returns:
        (ok: bool, message: str). On success, `message` is the staged
        bundle's absolute path.
    """
    os.makedirs(STAGING_DIR, exist_ok=True)
    dst = bundle_path()
    tmp = dst + ".partial"

    write_status(STATE_DOWNLOADING, progress=0)
    received = 0
    chunk = 64 * 1024

    try:
        with open(tmp, "wb") as f:
            while received < total_bytes:
                buf = src_fileobj.read(min(chunk, total_bytes - received))
                if not buf:
                    break
                f.write(buf)
                received += len(buf)
                if progress_cb is not None:
                    try:
                        progress_cb(received, total_bytes)
                    except Exception as cb_exc:
                        log.debug("progress_cb raised: %s", cb_exc)
                # Coarse progress: download occupies 0..40% of overall bar.
                pct = int((received / total_bytes) * 40) if total_bytes else 0
                write_status(STATE_DOWNLOADING, progress=pct)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        write_status(STATE_ERROR, error=f"Write failed: {exc}")
        return False, f"Write failed: {exc}"

    if received != total_bytes:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        write_status(STATE_ERROR, error="Incomplete upload")
        return False, "Incomplete upload"

    try:
        os.replace(tmp, dst)
    except OSError as exc:
        write_status(STATE_ERROR, error=f"Rename failed: {exc}")
        return False, f"Rename failed: {exc}"

    # Surface the target version so the UI can render "1.0.0 → 1.1.0"
    # alongside the staged bundle. Empty string is acceptable — older
    # bundles without a version field still install.
    target_version = extract_bundle_version(dst)
    write_status(STATE_DOWNLOADING, progress=40, target_version=target_version)
    log.info(
        "Bundle staged at %s (%d bytes, version=%s)",
        dst,
        received,
        target_version or "unknown",
    )
    return True, dst


def trigger_install(bundle=None):
    """Fire the install trigger. The root camera-ota-installer.service
    is activated by a systemd .path unit watching TRIGGER_PATH.

    Args:
        bundle: optional absolute path to bundle. Defaults to the
            canonical staged path.

    Returns:
        (ok: bool, message: str).
    """
    bundle = bundle or bundle_path()
    if not os.path.isfile(bundle):
        return False, f"Bundle missing: {bundle}"

    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
    except OSError as exc:
        return False, f"Spool dir inaccessible: {exc}"

    # Write trigger atomically. Contents: absolute bundle path on line 1.
    try:
        fd, tmp = tempfile.mkstemp(prefix=".trigger.", dir=SPOOL_DIR)
        with os.fdopen(fd, "w") as f:
            f.write(bundle + "\n")
        os.chmod(tmp, 0o664)
        os.replace(tmp, TRIGGER_PATH)
    except OSError as exc:
        return False, f"Could not write trigger: {exc}"

    # Reset status so polling sees the new install cycle, not stale data.
    write_status(STATE_VERIFYING, progress=5)
    log.info("Install trigger written for %s", bundle)
    return True, "Install triggered"


def trigger_reboot():
    """Request a reboot via the trigger-file protocol.

    The camera-streamer service runs as ``User=camera`` with no shell
    and no CAP_SYS_BOOT, so it cannot reboot directly —
    ``subprocess.run(["reboot"])`` from the camera user fails with
    "Failed to unlink reboot parameter file: Read-only file system"
    even on a writable rootfs (the legacy ``reboot`` binary needs
    privileges the user doesn't have).

    This mirrors the install pattern: write a trigger file under
    ``SPOOL_DIR``, and the root-privileged ``camera-ota-reboot.service``
    activated by ``camera-ota-reboot.path`` performs the actual
    ``systemctl reboot``.

    Caller must ensure ``status.json`` reports ``state="installed"``
    before calling — rebooting mid-install bricks the standby slot.
    The HTTP handler in ``status_server._handle_ota_reboot`` already
    enforces this check.

    Returns:
        (ok: bool, message: str). On error the camera-streamer caller
        logs the message and surfaces a 500 to the dashboard; the
        device itself is unharmed.
    """
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
    except OSError as exc:
        return False, f"Spool dir inaccessible: {exc}"

    # Atomic write: temp file + rename. ``PathModified=`` on the
    # reboot path-unit watches for IN_CLOSE_WRITE / IN_MOVED_TO so
    # the rename suffices to fire the trigger.
    try:
        fd, tmp = tempfile.mkstemp(prefix=".reboot-trigger.", dir=SPOOL_DIR)
        with os.fdopen(fd, "w") as f:
            f.write(f"{int(time.time())}\n")
        os.chmod(tmp, 0o664)
        os.replace(tmp, REBOOT_TRIGGER_PATH)
    except OSError as exc:
        return False, f"Could not write reboot trigger: {exc}"

    log.info("Reboot trigger written")
    return True, "Reboot triggered"


def wait_for_completion(timeout=900, poll_interval=2):
    """Block until the installer reaches a terminal state.

    Returns:
        Final status dict. Includes `state` in {installed, error}
        unless timeout fired, in which case state may still be
        'installing'.
    """
    deadline = time.time() + timeout
    start_seen = False
    start_deadline = time.time() + TRIGGER_START_TIMEOUT
    while time.time() < deadline:
        status = read_status()
        state = status.get("state")
        if state in (STATE_INSTALLED, STATE_ERROR):
            return status
        if state in (STATE_VERIFYING, STATE_INSTALLING):
            start_seen = True
        elif not start_seen and time.time() > start_deadline:
            # Trigger fired but installer never started: path unit
            # probably not enabled, or permissions broken.
            return {
                "state": STATE_ERROR,
                "progress": 0,
                "error": (
                    "Installer did not start within "
                    f"{TRIGGER_START_TIMEOUT}s — check camera-ota-installer.path"
                ),
            }
        time.sleep(poll_interval)
    return read_status()
