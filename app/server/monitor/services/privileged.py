# REQ: SWR-027, SWR-028, SWR-063; RISK: RISK-013, RISK-018; SEC: SC-013, SC-019; TEST: TC-024, TC-025, TC-044
"""Client for the root-only monitor privilege helper.

The Flask app must not run as root just to perform occasional appliance
operations. This module gives services a small JSON-over-Unix-socket client
for explicitly allowlisted helper actions.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Any

HELPER_SOCKET = os.environ.get(
    "MONITOR_PRIVILEGED_HELPER_SOCKET",
    "/run/monitor/privileged-helper.sock",
)
HELPER_TIMEOUT_SECONDS = 35


class PrivilegedHelperError(RuntimeError):
    """Raised when the privileged helper is unavailable or rejects a request."""


def should_use_helper() -> bool:
    """Return True when the current process is unprivileged on a POSIX host."""
    if os.environ.get("MONITOR_DISABLE_PRIVILEGED_HELPER") == "1":
        return False
    return (
        os.name == "posix"
        and hasattr(os, "geteuid")
        and os.geteuid() != 0
        and "MONITOR_PRIVILEGED_HELPER_SOCKET" in os.environ
    )


def is_helper_available(socket_path: str = HELPER_SOCKET) -> bool:
    return (
        hasattr(socket, "AF_UNIX")
        and os.path.exists(socket_path)
        and os.path.exists(os.path.dirname(socket_path))
    )


def request(
    operation: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = HELPER_TIMEOUT_SECONDS,
    socket_path: str = HELPER_SOCKET,
) -> dict[str, Any]:
    """Send an allowlisted operation to the root helper.

    Returns the helper's ``data`` object. Raises ``PrivilegedHelperError`` with
    the helper-provided error string on rejection or transport failure.
    """
    if not is_helper_available(socket_path):
        raise PrivilegedHelperError("privileged helper unavailable")

    message = json.dumps(
        {"operation": operation, "payload": payload or {}},
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path)
            sock.sendall(message + b"\n")
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as exc:
        raise PrivilegedHelperError(str(exc)) from exc

    try:
        response = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivilegedHelperError("invalid privileged helper response") from exc

    if not isinstance(response, dict) or not response.get("ok"):
        error = ""
        if isinstance(response, dict):
            error = str(response.get("error") or "")
        raise PrivilegedHelperError(error or "privileged helper rejected request")
    data = response.get("data") or {}
    return data if isinstance(data, dict) else {}


def request_result(
    operation: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = HELPER_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Convenience wrapper for services that expose ``(ok, error)`` tuples."""
    try:
        request(operation, payload, timeout=timeout)
        return True, ""
    except PrivilegedHelperError as exc:
        return False, str(exc)
