# REQ: SWR-031; RISK: RISK-017; SEC: SC-016; TEST: TC-028
"""Tests for security-sensitive nginx routing choices."""

import re
from pathlib import Path

NGINX_CONFIG = Path(__file__).resolve().parents[2] / "config" / "nginx-monitor.conf"


def _location_blocks_matching(path_fragment: str) -> list[str]:
    text = NGINX_CONFIG.read_text(encoding="utf-8")
    blocks = []
    pattern = re.compile(
        r"(?P<header>location\s+[^{]*)\{(?P<body>.*?)\n\s*\}", re.DOTALL
    )
    for match in pattern.finditer(text):
        header = match.group("header")
        if path_fragment in header:
            blocks.append(match.group("body"))
    return blocks


def test_webrtc_path_is_not_proxied_directly_to_mediamtx():
    """The browser /webrtc path must reach Flask auth before MediaMTX."""
    for block in _location_blocks_matching("/webrtc"):
        assert "127.0.0.1:8889" not in block


def test_api_upstream_startup_fallback_is_json():
    """API callers must not receive the HTML startup page during reboots."""
    text = NGINX_CONFIG.read_text(encoding="utf-8")

    assert "location @api_starting" in text
    assert "default_type application/json" in text
    assert 'return 503 \'{"error":"Home Monitor is restarting' in text

    api_blocks = _location_blocks_matching("/api/")
    assert api_blocks
    api_block = "\n".join(api_blocks)
    assert "proxy_intercept_errors on" in api_block
    assert "error_page 502 503 504 = @api_starting" in api_block
