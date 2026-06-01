# REQ: SWR-018, SWR-050; RISK: RISK-006, RISK-018; SEC: SC-006, SC-019; TEST: TC-015, TC-044
"""Root-only allowlisted helper for camera appliance operations."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
from collections.abc import Callable
from typing import Any

from camera_streamer.privileged import HELPER_SOCKET

try:
    import grp
except ImportError:  # pragma: no cover - non-POSIX development hosts
    grp = None

try:
    import pwd
except ImportError:  # pragma: no cover - non-POSIX development hosts
    pwd = None

log = logging.getLogger("camera-streamer.privileged-helper")

MAX_REQUEST_BYTES = 8192
REQUEST_TIMEOUT_SECONDS = 120
SOCKET_GROUP = "camera"
SETUP_HOTSPOT_PASSWORD_FILE = "/data/config/camera-hotspot.psk"
BLOCKED_SETUP_HOTSPOT_PASSWORDS = {"homecamera", "homemonitor"}
MIN_SETUP_HOTSPOT_PASSWORD_LENGTH = 12
MAX_SETUP_HOTSPOT_PASSWORD_LENGTH = 63
LEDCTL = "/usr/bin/home-monitor-ledctl"
CAMERA_HOSTNAME_FILE = "/data/config/hostname"
HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
LED_STATES = {
    "boot",
    "healthy",
    "setup",
    "pairing",
    "connecting",
    "ota-installing",
    "ota-rebooting",
    "ota-validating",
    "reset",
    "error",
    "off",
}


class HelperRequestError(ValueError):
    """Raised when an operation is rejected before execution."""


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


def _op_hotspot_wipe(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(
        ["/opt/camera/scripts/camera-hotspot.sh", "wipe"],
        timeout=30,
        nonzero_ok=True,
    )


def _op_hotspot_start(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(
        ["systemctl", "restart", "camera-hotspot.service"],
        timeout=90,
    )


def _op_hotspot_stop(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(
        ["/opt/camera/scripts/camera-hotspot.sh", "stop"],
        timeout=30,
        nonzero_ok=True,
    )


def _op_hotspot_connect(payload: dict[str, Any]) -> dict[str, Any]:
    ssid = str(payload.get("ssid") or "").strip()
    password = str(payload.get("password") or "")
    if not ssid:
        raise HelperRequestError("ssid required")
    if not password:
        raise HelperRequestError("wifi password required")
    return _run_command(
        ["/opt/camera/scripts/camera-hotspot.sh", "connect", ssid, password],
        timeout=75,
    )


def _camera_identity() -> tuple[int, int] | None:
    if pwd is None or grp is None:
        return None
    try:
        uid = pwd.getpwnam(SOCKET_GROUP).pw_uid
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
    except KeyError as exc:
        raise HelperRequestError("camera service account not found") from exc
    return uid, gid


def _validate_setup_hotspot_password(value: Any) -> str:
    password = str(value or "").strip()
    if password.lower() in BLOCKED_SETUP_HOTSPOT_PASSWORDS:
        raise HelperRequestError("choose a new setup hotspot password")
    if len(password) < MIN_SETUP_HOTSPOT_PASSWORD_LENGTH:
        raise HelperRequestError(
            "setup hotspot password must be at least "
            f"{MIN_SETUP_HOTSPOT_PASSWORD_LENGTH} characters"
        )
    if len(password) > MAX_SETUP_HOTSPOT_PASSWORD_LENGTH:
        raise HelperRequestError(
            "setup hotspot password must be no more than "
            f"{MAX_SETUP_HOTSPOT_PASSWORD_LENGTH} characters"
        )
    return password


def _op_hotspot_set_password(payload: dict[str, Any]) -> dict[str, Any]:
    password = _validate_setup_hotspot_password(payload.get("password"))
    target = SETUP_HOTSPOT_PASSWORD_FILE
    parent = os.path.dirname(target)
    tmp_path = f"{target}.tmp"
    identity = _camera_identity()

    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(password + "\n")
        os.chmod(tmp_path, 0o600)
        if identity is not None:
            os.chown(tmp_path, identity[0], identity[1])
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
        if identity is not None:
            os.chown(target, identity[0], identity[1])
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise HelperRequestError(str(exc)) from exc
    return {"returncode": 0, "path": target}


def _op_led_set(payload: dict[str, Any]) -> dict[str, Any]:
    state = str(payload.get("state") or "").strip().replace("_", "-")
    if state not in LED_STATES:
        raise HelperRequestError("LED state is not allowed")
    role = str(payload.get("role") or "camera").strip()
    if role not in {"server", "camera"}:
        raise HelperRequestError("LED role is invalid")
    cmd = [LEDCTL, state, "--role", role]
    if bool(payload.get("force")):
        cmd.append("--force")
    if bool(payload.get("init")):
        cmd.append("--init")
    if bool(payload.get("clear_activation")):
        cmd.append("--clear-activation")
    return _run_command(cmd, timeout=10, nonzero_ok=True)


def _validate_hostname(value: Any) -> str:
    hostname = str(value or "").strip().lower()
    if not HOSTNAME_RE.match(hostname):
        raise HelperRequestError(
            "hostname must be 1-63 lowercase letters, digits, or hyphens"
        )
    return hostname


def _persist_camera_hostname(hostname: str) -> None:
    parent = os.path.dirname(CAMERA_HOSTNAME_FILE)
    tmp_path = f"{CAMERA_HOSTNAME_FILE}.tmp"
    identity = _camera_identity()
    os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(hostname + "\n")
        os.chmod(tmp_path, 0o644)
        if identity is not None:
            os.chown(tmp_path, identity[0], identity[1])
        os.replace(tmp_path, CAMERA_HOSTNAME_FILE)
        os.chmod(CAMERA_HOSTNAME_FILE, 0o644)
        if identity is not None:
            os.chown(CAMERA_HOSTNAME_FILE, identity[0], identity[1])
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise HelperRequestError(str(exc)) from exc


def _avahi_result_text(result: dict[str, Any]) -> str:
    return str(result.get("stderr") or result.get("stdout") or "").strip()


def _is_avahi_redundant(result: dict[str, Any]) -> bool:
    return "redundant" in _avahi_result_text(result).lower()


def _op_hostname_set(payload: dict[str, Any]) -> dict[str, Any]:
    hostname = _validate_hostname(payload.get("hostname"))
    _persist_camera_hostname(hostname)

    kernel = _run_command(["hostname", hostname], timeout=10)
    avahi = _run_command(
        ["avahi-set-host-name", hostname],
        timeout=10,
        nonzero_ok=True,
    )
    avahi_ok = int(avahi.get("returncode", 1)) == 0
    if not avahi_ok and _is_avahi_redundant(avahi):
        avahi_ok = True
    if not avahi_ok:
        log.warning(
            "avahi-set-host-name rejected %s: %s",
            hostname,
            _avahi_result_text(avahi),
        )
        _run_command(
            ["systemctl", "restart", "avahi-daemon"],
            timeout=20,
            nonzero_ok=True,
        )

    return {
        "hostname": hostname,
        "kernel_returncode": kernel["returncode"],
        "avahi_returncode": avahi.get("returncode", 1),
        "avahi_effective": "ok" if avahi_ok else "restarted",
    }


def _op_system_reboot(payload: dict[str, Any]) -> dict[str, Any]:
    return _run_command(["systemctl", "reboot"], timeout=15)


OPERATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "hotspot.connect": _op_hotspot_connect,
    "hotspot.set_password": _op_hotspot_set_password,
    "hotspot.wipe": _op_hotspot_wipe,
    "hotspot.start": _op_hotspot_start,
    "hotspot.stop": _op_hotspot_stop,
    "hostname.set": _op_hostname_set,
    "led.set": _op_led_set,
    "system.reboot": _op_system_reboot,
}


def _handle_request(raw: bytes) -> bytes:
    if len(raw) > MAX_REQUEST_BYTES:
        raise HelperRequestError("request too large")
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HelperRequestError("invalid JSON") from exc

    if not isinstance(message, dict):
        raise HelperRequestError("request must be an object")
    operation = str(message.get("operation") or "")
    payload = message.get("payload") or {}
    if not isinstance(payload, dict):
        raise HelperRequestError("payload must be an object")
    handler = OPERATIONS.get(operation)
    if handler is None:
        raise HelperRequestError("operation not allowed")
    data = handler(payload)
    return json.dumps({"ok": True, "data": data}, separators=(",", ":")).encode("utf-8")


def _set_socket_permissions(socket_path: str) -> None:
    os.chmod(socket_path, 0o660)
    if grp is None:
        return
    try:
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
    except KeyError:
        return
    os.chown(socket_path, 0, gid)


def serve(socket_path: str = HELPER_SOCKET) -> None:
    os.makedirs(os.path.dirname(socket_path), mode=0o750, exist_ok=True)
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(socket_path)
        _set_socket_permissions(socket_path)
        server.listen(8)
        log.info("Camera privileged helper listening on %s", socket_path)
        while True:
            conn, _ = server.accept()
            with conn:
                conn.settimeout(REQUEST_TIMEOUT_SECONDS)
                try:
                    raw = conn.recv(MAX_REQUEST_BYTES + 1)
                    response = _handle_request(raw.strip())
                except Exception as exc:
                    log.warning("Privileged helper request rejected: %s", exc)
                    response = json.dumps(
                        {"ok": False, "error": str(exc)},
                        separators=(",", ":"),
                    ).encode("utf-8")
                conn.sendall(response + b"\n")


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
