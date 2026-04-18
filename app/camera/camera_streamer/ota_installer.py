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
import tempfile
import time

log = logging.getLogger("camera-streamer.ota-installer")

SPOOL_DIR = "/var/lib/camera-ota"
STAGING_DIR = os.path.join(SPOOL_DIR, "staging")
TRIGGER_PATH = os.path.join(SPOOL_DIR, "trigger")
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
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return {"state": STATE_IDLE, "progress": 0, "error": ""}


def write_status(state, progress=0, error=""):
    """Write a status transition from the client side (e.g. during the
    'downloading' phase, before the root installer takes over).

    Writes atomically via rename so readers never see a half-written
    JSON document.
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
    data = json.dumps(payload).encode()
    try:
        fd, tmp = tempfile.mkstemp(
            prefix=".status.", suffix=".json", dir=SPOOL_DIR
        )
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

    log.info("Bundle staged at %s (%d bytes)", dst, received)
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
