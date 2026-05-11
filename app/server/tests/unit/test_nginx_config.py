# REQ: SWR-031; RISK: RISK-017; SEC: SC-016; TEST: TC-028
"""Tests for security-sensitive nginx routing choices."""

import re
from pathlib import Path

NGINX_CONFIG = Path(__file__).resolve().parents[2] / "config" / "nginx-monitor.conf"


def _location_blocks_matching(path_fragment: str) -> list[str]:
    text = NGINX_CONFIG.read_text(encoding="utf-8")
    blocks = []
    pattern = re.compile(r"location\s+[^{]*\{(?P<body>.*?)\n\s*\}", re.DOTALL)
    for match in pattern.finditer(text):
        header = text[: match.start()].rsplit("\n", 1)[-1]
        if path_fragment in header:
            blocks.append(match.group("body"))
    return blocks


def test_webrtc_path_is_not_proxied_directly_to_mediamtx():
    """The browser /webrtc path must reach Flask auth before MediaMTX."""
    for block in _location_blocks_matching("/webrtc"):
        assert "127.0.0.1:8889" not in block
