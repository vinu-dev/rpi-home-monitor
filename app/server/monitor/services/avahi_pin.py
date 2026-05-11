# REQ: SWR-024, SWR-050; RISK: RISK-010, RISK-012; SEC: SC-012; TEST: TC-023
"""Pin the server's mDNS identity before avahi-daemon starts.

The server can have Ethernet and WiFi on the same LAN. Publishing the same
hostname on both links can make Avahi detect a self-conflict and rename the
host to ``rpi-divinu-2.local``. Cameras depend on the fixed
``rpi-divinu.local`` name, so the server image runs this module as a small
oneshot service before avahi-daemon and points Avahi at the generated config
under /data.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from monitor.services.provisioning_service import SERVER_HOSTNAME

log = logging.getLogger("monitor.services.avahi_pin")

DEFAULT_CONFIG_PATH = Path("/data/config/avahi-daemon.conf")
DEFAULT_PREFERRED_INTERFACES = ("eth0", "wlan0")
VIRTUAL_INTERFACE_PREFIXES = (
    "br-",
    "docker",
    "lo",
    "tailscale",
    "tun",
    "veth",
    "virbr",
    "wg",
)
HOST_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
IFACE_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")
SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
PINNED_KEY_RE = re.compile(r"^\s*#?\s*(host-name|allow-interfaces)\s*=")

CommandRunner = Callable[[list[str]], str]


def _command_output(argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Command unavailable for Avahi pinning: %s (%s)", argv, exc)
        return ""
    if result.returncode != 0:
        log.debug("Command failed for Avahi pinning: %s (%s)", argv, result.stderr)
        return ""
    return result.stdout


def _normalise_hostname(host_name: str) -> str:
    host = host_name.strip().removesuffix(".local")
    if not HOST_RE.match(host):
        raise ValueError(f"invalid Avahi host name: {host_name!r}")
    return host


def _normalise_interface(interface: str) -> str:
    iface = interface.strip()
    if iface and not IFACE_RE.match(iface):
        raise ValueError(f"invalid Avahi interface: {interface!r}")
    return iface


def _preferred_interfaces_from_env() -> tuple[str, ...]:
    raw = os.environ.get("MONITOR_AVAHI_PREFERRED_INTERFACES", "")
    if not raw.strip():
        return DEFAULT_PREFERRED_INTERFACES
    return tuple(part for part in raw.replace(",", " ").split() if part)


def _default_route_interface(run: CommandRunner = _command_output) -> str:
    output = run(["ip", "-4", "route", "show", "default"])
    for line in output.splitlines():
        match = re.search(r"\bdev\s+(\S+)", line)
        if match:
            return match.group(1)
    return ""


def _interface_has_ipv4(iface: str, run: CommandRunner = _command_output) -> bool:
    iface = _normalise_interface(iface)
    if not iface:
        return False
    output = run(["ip", "-4", "-o", "addr", "show", "dev", iface, "scope", "global"])
    return " inet " in f" {output} "


def _interface_exists(iface: str, run: CommandRunner = _command_output) -> bool:
    iface = _normalise_interface(iface)
    if not iface:
        return False
    return bool(run(["ip", "link", "show", "dev", iface]))


def _is_physical_candidate(iface: str, preferred: tuple[str, ...]) -> bool:
    if not iface:
        return False
    if iface in preferred:
        return True
    return not iface.startswith(VIRTUAL_INTERFACE_PREFIXES)


def choose_publish_interface(
    preferred: tuple[str, ...] | None = None,
    run: CommandRunner = _command_output,
) -> str:
    """Choose one interface so Avahi cannot self-conflict across LAN links."""
    candidates = preferred or _preferred_interfaces_from_env()
    default_iface = _default_route_interface(run)
    if (
        _is_physical_candidate(default_iface, candidates)
        and default_iface in candidates
        and _interface_has_ipv4(default_iface, run)
    ):
        return default_iface

    for iface in candidates:
        if _interface_has_ipv4(iface, run):
            return iface

    for iface in candidates:
        if _interface_exists(iface, run):
            return iface

    if _is_physical_candidate(default_iface, candidates):
        return default_iface

    return candidates[0] if candidates else ""


def _pinned_lines(host_name: str, interface: str) -> list[str]:
    host = _normalise_hostname(host_name)
    iface = _normalise_interface(interface)
    lines = [f"host-name={host}"]
    if iface:
        lines.append(f"allow-interfaces={iface}")
    return lines


def render_avahi_config(existing: str, host_name: str, interface: str) -> str:
    """Return Avahi config text with pinned [server] identity settings."""
    pinned = _pinned_lines(host_name, interface)
    lines = existing.splitlines()
    output: list[str] = []
    in_server = False
    saw_server = False

    for line in lines:
        section = SECTION_RE.match(line)
        if section:
            in_server = section.group(1).strip().lower() == "server"
            saw_server = saw_server or in_server
            output.append(line)
            if in_server:
                output.extend(pinned)
            continue
        if in_server and PINNED_KEY_RE.match(line):
            continue
        output.append(line)

    if not saw_server:
        output = [
            "[server]",
            *pinned,
            "use-ipv4=yes",
            "use-ipv6=yes",
            "",
            "[publish]",
            "publish-addresses=yes",
            "",
            *output,
        ]

    return "\n".join(output).rstrip() + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if current == content:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    if current and not path.with_suffix(path.suffix + ".pre-monitor").exists():
        path.with_suffix(path.suffix + ".pre-monitor").write_text(
            current,
            encoding="utf-8",
        )
    path.write_text(content, encoding="utf-8")
    return True


def apply_avahi_pin(
    *,
    config_path: Path | None = None,
    host_name: str | None = None,
    preferred: tuple[str, ...] | None = None,
    run: CommandRunner = _command_output,
) -> tuple[bool, str]:
    path = config_path or Path(
        os.environ.get("MONITOR_AVAHI_CONFIG", DEFAULT_CONFIG_PATH)
    )
    host = host_name or os.environ.get("MONITOR_AVAHI_HOST_NAME", SERVER_HOSTNAME)
    interface = choose_publish_interface(preferred, run=run)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    changed = write_if_changed(path, render_avahi_config(existing, host, interface))
    return changed, interface


def main() -> int:
    changed, interface = apply_avahi_pin()
    action = "updated" if changed else "already pinned"
    log.warning(
        "Avahi mDNS identity %s: %s.local on %s", action, SERVER_HOSTNAME, interface
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
