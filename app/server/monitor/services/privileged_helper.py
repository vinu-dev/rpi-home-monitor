# REQ: SWR-027, SWR-028, SWR-063; RISK: RISK-013, RISK-018; SEC: SC-013, SC-019; TEST: TC-024, TC-025, TC-044
"""Root-only allowlisted helper for appliance operations.

This process is started by systemd as root and listens on a group-restricted
Unix socket. The Flask app runs as the unprivileged ``monitor`` user and can
ask this helper for narrowly-scoped operations that truly need root.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import re
import signal
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from monitor.services import usb
from monitor.services.privileged import HELPER_SOCKET

try:
    import grp
except ImportError:  # pragma: no cover - non-POSIX development hosts
    grp = None

try:
    import pwd
except ImportError:  # pragma: no cover - non-POSIX development hosts
    pwd = None

log = logging.getLogger("monitor.privileged-helper")

MAX_REQUEST_BYTES = 8192
REQUEST_TIMEOUT_SECONDS = 660
DEVICE_RE = re.compile(r"^/dev/sd[a-z][0-9]*$")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
TIMEZONE_RE = re.compile(r"^[A-Za-z0-9_+./-]{1,80}$")
ISO_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
LABEL_RE = re.compile(r"^[A-Za-z0-9_. -]{1,32}$")
OTA_PUBLIC_KEY = "/etc/swupdate-public.crt"


class HelperRequestError(ValueError):
    """Raised when an operation is rejected before execution."""


def _as_text(value: Any, *, max_len: int = 256) -> str:
    if not isinstance(value, str):
        raise HelperRequestError("value must be a string")
    value = value.strip()
    if not value or len(value) > max_len:
        raise HelperRequestError("value length is invalid")
    return value


def _validate_device_path(value: Any) -> str:
    path = _as_text(value, max_len=32)
    if "mmcblk" in path or not DEVICE_RE.fullmatch(path):
        raise HelperRequestError("device path is not an allowed USB block device")
    devices = {device.get("path") for device in usb.detect_devices()}
    if path not in devices:
        raise HelperRequestError(
            "device path was not detected as removable USB storage"
        )
    return path


def _validate_mount_point(value: Any) -> str:
    mount_point = _as_text(value or usb.DEFAULT_MOUNT_POINT, max_len=64)
    if mount_point != usb.DEFAULT_MOUNT_POINT:
        raise HelperRequestError("mount point is not allowlisted")
    return mount_point


def _run_command(
    cmd: list[str],
    *,
    timeout: int = 30,
    nonzero_ok: bool = False,
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HelperRequestError(f"{cmd[0]} not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise HelperRequestError(f"{cmd[0]} timed out") from exc
    except OSError as exc:
        raise HelperRequestError(str(exc)) from exc

    data = {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.returncode != 0 and not nonzero_ok:
        raise HelperRequestError(result.stderr.strip() or result.stdout.strip())
    return data


def _op_usb_mount(payload: dict[str, Any]) -> dict[str, Any]:
    mount_point = _validate_mount_point(payload.get("mount_point"))
    ok, err = usb.mount_device(
        _validate_device_path(payload.get("device_path")),
        mount_point,
    )
    if not ok:
        raise HelperRequestError(err or "mount failed")
    _ensure_monitor_can_write_mount(mount_point)
    return {}


def _ensure_monitor_can_write_mount(mount_point: str) -> None:
    """Make a helper-mounted USB root writable by ``monitor``.

    The helper runs as root, so ``usb.mount_device`` cannot infer the Flask
    service UID from ``os.getuid()``. Without this correction ext4 USB drives
    mount as root-owned and the unprivileged app cannot create the recordings
    folder, causing the Storage tab's "Use" action to fail after a successful
    mount.
    """
    if pwd is None or grp is None:
        return
    try:
        uid = pwd.getpwnam("monitor").pw_uid
        gid = grp.getgrnam("monitor").gr_gid
        os.chown(mount_point, uid, gid)
        os.chmod(mount_point, 0o775)
        recordings_dir = posixpath.join(mount_point, usb.RECORDINGS_FOLDER)
        os.makedirs(recordings_dir, exist_ok=True)
        os.chown(recordings_dir, uid, gid)
        os.chmod(recordings_dir, 0o775)
    except (KeyError, PermissionError, OSError) as exc:
        raise HelperRequestError(
            f"USB mounted but {mount_point} is not writable by monitor: {exc}"
        ) from exc


def _op_usb_unmount(payload: dict[str, Any]) -> dict[str, Any]:
    ok, err = usb.unmount_device(_validate_mount_point(payload.get("mount_point")))
    if not ok:
        raise HelperRequestError(err or "unmount failed")
    return {}


def _op_usb_format(payload: dict[str, Any]) -> dict[str, Any]:
    label = str(payload.get("label") or "HomeMonitor").strip()
    if not LABEL_RE.fullmatch(label):
        raise HelperRequestError("filesystem label is invalid")
    ok, err = usb.format_device(
        _validate_device_path(payload.get("device_path")),
        fstype="ext4",
        label=label,
    )
    if not ok:
        raise HelperRequestError(err or "format failed")
    return {}


def _op_time_set_timezone(payload: dict[str, Any]) -> dict[str, Any]:
    timezone = _as_text(payload.get("timezone"), max_len=80)
    if not TIMEZONE_RE.fullmatch(timezone):
        raise HelperRequestError("timezone is invalid")
    return _run_command(["timedatectl", "set-timezone", timezone], timeout=10)


def _op_time_set_ntp(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(payload.get("enabled"))
    return _run_command(["timedatectl", "set-ntp", "true" if enabled else "false"])


def _op_time_set_manual(payload: dict[str, Any]) -> dict[str, Any]:
    stamp = _as_text(payload.get("stamp"), max_len=19)
    if not ISO_STAMP_RE.fullmatch(stamp):
        raise HelperRequestError("manual time stamp is invalid")
    return _run_command(["timedatectl", "set-time", stamp], timeout=10)


def _op_time_restart_timesyncd(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(["systemctl", "restart", "systemd-timesyncd"], timeout=10)


def _op_network_connect_wifi(payload: dict[str, Any]) -> dict[str, Any]:
    ssid = _as_text(payload.get("ssid"), max_len=64)
    password = _as_text(payload.get("password"), max_len=128)
    return _run_command(
        [
            "nmcli",
            "device",
            "wifi",
            "connect",
            ssid,
            "password",
            password,
            "ifname",
            "wlan0",
        ],
        timeout=30,
        nonzero_ok=True,
    )


def _op_hotspot_connect_wifi(payload: dict[str, Any]) -> dict[str, Any]:
    ssid = _as_text(payload.get("ssid"), max_len=64)
    password = _as_text(payload.get("password"), max_len=128)
    return _run_command(
        ["/opt/monitor/scripts/monitor-hotspot.sh", "connect", ssid, password],
        timeout=45,
        nonzero_ok=True,
    )


def _op_hotspot_stop(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(
        ["/opt/monitor/scripts/monitor-hotspot.sh", "stop"],
        timeout=20,
        nonzero_ok=True,
    )


def _op_hotspot_start(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(
        ["systemctl", "restart", "monitor-hotspot.service"],
        timeout=90,
    )


def _op_hostname_set(payload: dict[str, Any]) -> dict[str, Any]:
    hostname = _as_text(payload.get("hostname"), max_len=63)
    if not HOSTNAME_RE.fullmatch(hostname):
        raise HelperRequestError("hostname is invalid")
    return _run_command(["hostnamectl", "set-hostname", hostname], timeout=10)


def _op_tailscale_up(payload: dict[str, Any]) -> dict[str, Any]:
    cmd = ["tailscale", "up", "--timeout=5s"]
    if bool(payload.get("accept_routes")):
        cmd.append("--accept-routes")
    if bool(payload.get("ssh")):
        cmd.append("--ssh")
    authkey = str(payload.get("authkey") or "").strip()
    if authkey:
        if len(authkey) > 256 or any(ch.isspace() for ch in authkey):
            raise HelperRequestError("tailscale auth key is invalid")
        cmd.append(f"--authkey={authkey}")
    return _run_command(cmd, timeout=15, nonzero_ok=True)


def _op_tailscale_down(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(["tailscale", "down"], timeout=15, nonzero_ok=True)


def _op_tailscale_enable(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(["systemctl", "enable", "--now", "tailscaled"], timeout=15)


def _op_tailscale_disable(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(["systemctl", "disable", "--now", "tailscaled"], timeout=15)


def _validate_ota_bundle_path(value: Any) -> str:
    raw_path = _as_text(value, max_len=512)
    if not raw_path.startswith("/"):
        raise HelperRequestError("OTA bundle path is not allowlisted")
    if os.name == "nt":  # keeps the Linux path contract testable on dev hosts.
        path = posixpath.normpath(raw_path)
    else:
        path = str(Path(raw_path).resolve(strict=False))
    if (
        not path.startswith("/data/ota/")
        or Path(path).suffix.lower() != ".swu"
        or path == "/data/ota"
    ):
        raise HelperRequestError("OTA bundle path is not allowlisted")
    return path


def _validate_ota_public_key_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    if path != OTA_PUBLIC_KEY:
        raise HelperRequestError("OTA public key path is not allowlisted")
    return path


def _op_ota_verify(payload: dict[str, Any]) -> dict[str, Any]:
    cmd = [
        "swupdate",
        "-c",
        "-i",
        _validate_ota_bundle_path(payload.get("bundle_path")),
    ]
    public_key = _validate_ota_public_key_path(payload.get("public_key_path"))
    if public_key:
        cmd.extend(["-k", public_key])
    return _run_command(cmd, timeout=60, nonzero_ok=True)


def _op_ota_install(payload: dict[str, Any]) -> dict[str, Any]:
    cmd = ["swupdate", "-i", _validate_ota_bundle_path(payload.get("bundle_path"))]
    public_key = _validate_ota_public_key_path(payload.get("public_key_path"))
    if public_key:
        cmd.extend(["-k", public_key])
    return _run_command(cmd, timeout=600, nonzero_ok=True)


def _op_system_reboot(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(["systemctl", "reboot"], timeout=15)


OPERATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "usb.mount": _op_usb_mount,
    "usb.unmount": _op_usb_unmount,
    "usb.format": _op_usb_format,
    "time.set_timezone": _op_time_set_timezone,
    "time.set_ntp": _op_time_set_ntp,
    "time.set_manual": _op_time_set_manual,
    "time.restart_timesyncd": _op_time_restart_timesyncd,
    "network.connect_wifi": _op_network_connect_wifi,
    "hotspot.connect_wifi": _op_hotspot_connect_wifi,
    "hotspot.stop": _op_hotspot_stop,
    "hotspot.start": _op_hotspot_start,
    "hostname.set": _op_hostname_set,
    "tailscale.up": _op_tailscale_up,
    "tailscale.down": _op_tailscale_down,
    "tailscale.enable": _op_tailscale_enable,
    "tailscale.disable": _op_tailscale_disable,
    "ota.verify": _op_ota_verify,
    "ota.install": _op_ota_install,
    "system.reboot": _op_system_reboot,
}


def handle_request(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_REQUEST_BYTES:
        raise HelperRequestError("request too large")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HelperRequestError("invalid JSON request") from exc
    if not isinstance(request, dict):
        raise HelperRequestError("request must be an object")
    operation = str(request.get("operation") or "")
    payload = request.get("payload") or {}
    if not isinstance(payload, dict):
        raise HelperRequestError("payload must be an object")
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise HelperRequestError("operation is not allowlisted")
    return handler(payload)


def _set_socket_permissions(socket_path: str) -> None:
    os.chmod(socket_path, 0o660)
    if grp is None:
        log.warning("grp module unavailable; helper socket remains root-only")
        return
    try:
        group = grp.getgrnam("monitor")
    except KeyError:
        log.warning("monitor group missing; helper socket remains root-only")
        return
    os.chown(socket_path, 0, group.gr_gid)


def serve(socket_path: str = HELPER_SOCKET) -> None:
    logging.basicConfig(level=logging.INFO)
    path = Path(socket_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    stop = False

    def _stop(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(socket_path)
        _set_socket_permissions(socket_path)
        server.listen(8)
        server.settimeout(1.0)
        log.info("privileged helper listening on %s", socket_path)
        while not stop:
            try:
                conn, _addr = server.accept()
            except TimeoutError:
                continue
            with conn:
                conn.settimeout(REQUEST_TIMEOUT_SECONDS)
                try:
                    raw = conn.recv(MAX_REQUEST_BYTES + 1).splitlines()[0]
                    data = handle_request(raw)
                    response = {"ok": True, "data": data}
                except Exception as exc:
                    log.warning("privileged helper rejected request: %s", exc)
                    response = {"ok": False, "error": str(exc)}
                conn.sendall(json.dumps(response, separators=(",", ":")).encode())
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
