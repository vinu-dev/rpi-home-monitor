# REQ: SWR-238-F; RISK: RISK-238-6; SEC: SEC-238-A
# TEST: TC-238-AC-10
"""Tests for the LAN-vs-Tailscale-Funnel request classifier."""

import pytest

from monitor.services.request_origin import classify, is_remote


@pytest.mark.parametrize(
    "ip, expected",
    [
        ("192.168.1.10", "lan"),
        ("10.0.0.5", "lan"),
        ("172.16.4.2", "lan"),
        ("127.0.0.1", "lan"),
        ("8.8.8.8", "lan"),  # public-internet IP — not Tailscale, treat as LAN
        ("100.64.1.1", "tailscale_funnel"),  # CGNAT lower bound
        ("100.127.255.255", "tailscale_funnel"),  # within /10
        ("100.128.0.1", "lan"),  # just outside /10
    ],
)
def test_classify(ip, expected):
    assert classify(ip) == expected


def test_classify_blank_falls_back_to_remote():
    """Empty / unparseable inputs fail-safe to remote so the policy
    doesn't silently let an unclassifiable request through."""
    assert classify("") == "tailscale_funnel"
    assert classify(None) == "tailscale_funnel"
    assert classify("not-an-ip") == "tailscale_funnel"


def test_is_remote_predicate():
    assert is_remote("100.64.1.1") is True
    assert is_remote("192.168.1.1") is False
