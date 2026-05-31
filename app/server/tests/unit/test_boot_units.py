# REQ: SWR-050; RISK: RISK-018; SEC: SC-019; TEST: TC-044
"""Boot dependency policy tests for image-level systemd units."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_mediamtx_does_not_wait_for_network_online():
    text = _read("meta-home-monitor/recipes-multimedia/mediamtx/files/mediamtx.service")

    assert "network-online.target" not in text
    assert "After=network.target" in text
    assert "Before=monitor.service" in text


def test_tailscale_daemon_does_not_pull_wait_online():
    text = _read(
        "meta-home-monitor/recipes-connectivity/tailscale/files/tailscaled.service"
    )

    assert "network-online.target" not in text
    assert "NetworkManager-wait-online.service" not in text
    assert "After=NetworkManager.service network.target local-fs.target" in text
    assert "RequiresMountsFor=/data" in text


def test_image_masks_unused_systemd_networkd_wait_online():
    text = _read("meta-home-monitor/recipes-core/systemd/systemd-conf_%.bbappend")

    assert "systemd-networkd-wait-online.service" in text
    assert "ln -sf /dev/null" in text
