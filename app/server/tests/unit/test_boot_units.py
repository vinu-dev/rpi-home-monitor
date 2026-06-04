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


def test_monitor_certs_runs_before_services_that_need_runtime_certs():
    unit = _read(
        "meta-home-monitor/recipes-security/monitor-certs/files/monitor-certs.service"
    )
    recipe = _read(
        "meta-home-monitor/recipes-security/monitor-certs/monitor-certs_1.0.bb"
    )

    assert "file://monitor-certs.service" in recipe
    assert "ConditionPathExists=!/data/certs/ca.crt" not in unit
    assert "Before=nginx.service mediamtx.service monitor.service" in unit


def test_monitor_certs_repairs_server_key_permissions_every_boot():
    script = _read(
        "meta-home-monitor/recipes-security/monitor-certs/files/generate-certs.sh"
    )

    assert 'MONITOR_GROUP="${MONITOR_GROUP:-monitor}"' in script
    assert 'chown "root:$MONITOR_GROUP" "$SERVER_KEY"' in script
    assert 'chmod 0640 "$SERVER_KEY"' in script
    assert 'chown root:root "$CA_KEY"' in script
    assert 'chmod 0600 "$CA_KEY"' in script
    assert "Certificates already exist, repairing permissions." in script
    assert "repair_cert_permissions" in script


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
