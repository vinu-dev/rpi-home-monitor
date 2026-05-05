# REQ: SWR-024; RISK: RISK-012; SEC: SC-012; TEST: TC-023
"""Helpers for same-origin LAN fallback URLs shown in the UI."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _private_ipv4(address: str | None) -> str:
    """Return an RFC1918 IPv4 string or an empty string."""
    if not address:
        return ""
    try:
        ip = ipaddress.ip_address(address.strip())
    except (ValueError, AttributeError):
        return ""
    if ip.version != 4:
        return ""
    if not any(ip in network for network in _RFC1918_NETWORKS):
        return ""
    return str(ip)


def _port_for_host_url(host_url: str) -> int:
    parsed = urlsplit(host_url or "")
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _interface_ip_for_remote(remote_addr: str | None) -> str:
    """Return the local IPv4 chosen to reach the requester, if private."""
    target = _private_ipv4(remote_addr)
    if not target:
        return ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((target, 1))
            local_ip = sock.getsockname()[0]
    except OSError:
        return ""
    return _private_ipv4(local_ip)


def get_network_payload(host_url: str, remote_addr: str | None) -> dict[str, object]:
    """Derive the best LAN URL for the current request context."""
    parsed = urlsplit(host_url or "")
    request_host_ip = _private_ipv4(parsed.hostname)
    port = _port_for_host_url(host_url)

    if request_host_ip:
        return {
            "server_url": f"https://{request_host_ip}:{port}/",
            "ip": request_host_ip,
            "port": port,
            "source": "request_host",
        }

    iface_ip = _interface_ip_for_remote(remote_addr)
    if iface_ip:
        return {
            "server_url": f"https://{iface_ip}:{port}/",
            "ip": iface_ip,
            "port": port,
            "source": "wifi_iface",
        }

    return {"server_url": "", "ip": "", "port": port, "source": ""}
