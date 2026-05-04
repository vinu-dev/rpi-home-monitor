# REQ: SWR-020, SWR-024
# RISK: RISK-002, RISK-012
# SEC: SC-004, SC-012
# TEST: TC-010, TC-023
"""Request-origin classifier — LAN vs Tailscale Funnel.

Used by the "require 2FA for remote sessions" policy (issue #238).
Pure function on a Flask request: returns ``"lan"`` or
``"tailscale_funnel"``.

Classification strategy:
1. If the request IP falls inside Tailscale's documented CGNAT range
   (``100.64.0.0/10``), the request reached us via the Tailscale tun
   interface — that's either a Tailnet peer or a Funnel ingress.
2. Otherwise it's LAN.

We deliberately do NOT trust forwarded headers like ``Tailscale-User-
Login``: a non-Funnel request on the LAN could trivially set them.
The trusted signal is the source IP, which ProxyFix has already
unmasked from ``X-Forwarded-For`` in ``__init__.py``.

The classifier is fail-safe: when the IP can't be parsed, we treat
the request as remote-required so an ambiguous request doesn't
silently bypass the policy.
"""

from __future__ import annotations

import ipaddress

# Tailscale's CGNAT range — every Tailnet IP, including Funnel
# ingresses, lives in 100.64.0.0/10. Documented at
# https://tailscale.com/kb/1015/100.x-addresses .
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def classify(remote_addr: str | None) -> str:
    """Classify a request as ``"lan"`` or ``"tailscale_funnel"``.

    Falls back to ``"tailscale_funnel"`` when the address can't be
    parsed — see module docstring rationale.
    """
    if not remote_addr:
        return "tailscale_funnel"
    try:
        ip = ipaddress.ip_address(remote_addr.strip())
    except (ValueError, TypeError):
        return "tailscale_funnel"
    if ip.version == 4 and ip in _TAILSCALE_CGNAT:
        return "tailscale_funnel"
    return "lan"


def is_remote(remote_addr: str | None) -> bool:
    """Convenience predicate equivalent to ``classify(...) != "lan"``."""
    return classify(remote_addr) != "lan"
