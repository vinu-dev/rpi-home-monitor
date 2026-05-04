# REQ: SWR-062; RISK: RISK-001, RISK-008; TEST: TC-005, TC-047
"""Minimal systemd ``sd_notify`` helper for camera runtime watchdogs."""

import logging
import os
import socket

log = logging.getLogger("camera-streamer.sd_notify")

READY = b"READY=1"
STOPPING = b"STOPPING=1"
WATCHDOG = b"WATCHDOG=1"


def notify(message: bytes) -> None:
    """Best-effort send to systemd's notify socket."""
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            if notify_socket.startswith("@"):
                notify_socket = "\0" + notify_socket[1:]
            sock.connect(notify_socket)
            sock.sendall(message)
        finally:
            sock.close()
    except Exception:
        log.debug("sd_notify send failed", exc_info=True)
