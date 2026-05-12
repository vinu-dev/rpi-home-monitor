# REQ: SWR-021, SWR-036; RISK: RISK-010; SEC: SC-010; TEST: TC-021, TC-044
"""Regression tests for first-boot hotspot credential rotation."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SERVER_SCRIPT = REPO_ROOT / "app/server/config/monitor-hotspot.sh"
CAMERA_SCRIPT = REPO_ROOT / "app/camera/config/camera-hotspot.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_hotspot_scripts_allow_documented_first_boot_defaults_only():
    for path, default_assignment in (
        (SERVER_SCRIPT, 'HOTSPOT_DEFAULT_PASS="homemonitor"'),
        (CAMERA_SCRIPT, 'HOTSPOT_DEFAULT_PASS="homecamera"'),
    ):
        text = _read(path)
        assert default_assignment in text
        assert 'SOURCE="default"' in text
        assert 'wifi-sec.psk "${HOTSPOT_PASS}"' not in text


def test_rotated_hotspot_credentials_must_not_reuse_factory_defaults():
    for path in (SERVER_SCRIPT, CAMERA_SCRIPT):
        text = _read(path)
        assert "HOTSPOT_PASS_FILE=" in text
        assert "get_hotspot_pass()" in text
        assert "known public setup hotspot credential is not allowed" in text
        assert "setup hotspot credential must be at least 12 characters" in text
        assert 'wifi-sec.psk "${HOTSPOT_PASS_VALUE}"' in text


def test_factory_reset_wifi_wipe_keeps_provisioned_setup_credential():
    for path in (SERVER_SCRIPT, CAMERA_SCRIPT):
        text = _read(path)
        assert 'rm -f "$HOTSPOT_PASS_FILE"' not in text
