# REQ: SWR-024, SWR-050; RISK: RISK-010, RISK-012; SEC: SC-012; TEST: TC-023
"""Tests for the server mDNS identity pinning helper."""

from pathlib import Path

from monitor.services import avahi_pin

REPO_ROOT = Path(__file__).resolve().parents[4]


def _fake_ip(outputs):
    def run(argv):
        return outputs.get(tuple(argv), "")

    return run


def test_choose_publish_interface_prefers_default_physical_lan():
    run = _fake_ip(
        {
            ("ip", "-4", "route", "show", "default"): (
                "default via 192.168.1.1 dev eth0 proto dhcp\n"
            ),
            (
                "ip",
                "-4",
                "-o",
                "addr",
                "show",
                "dev",
                "eth0",
                "scope",
                "global",
            ): "2: eth0 inet 192.168.1.244/24 brd 192.168.1.255 scope global eth0\n",
        }
    )

    assert avahi_pin.choose_publish_interface(run=run) == "eth0"


def test_choose_publish_interface_ignores_vpn_default_and_uses_lan():
    run = _fake_ip(
        {
            ("ip", "-4", "route", "show", "default"): (
                "default dev tailscale0 scope link\n"
            ),
            (
                "ip",
                "-4",
                "-o",
                "addr",
                "show",
                "dev",
                "eth0",
                "scope",
                "global",
            ): "2: eth0 inet 192.168.1.244/24 brd 192.168.1.255 scope global eth0\n",
        }
    )

    assert avahi_pin.choose_publish_interface(run=run) == "eth0"


def test_render_avahi_config_pins_server_section_without_duplicate_keys():
    existing = """[server]
#host-name=old-name
allow-interfaces=eth0,wlan0
use-ipv4=yes

[publish]
publish-addresses=yes
"""

    rendered = avahi_pin.render_avahi_config(existing, "rpi-divinu.local", "eth0")

    assert "host-name=rpi-divinu\n" in rendered
    assert "allow-interfaces=eth0\n" in rendered
    assert "old-name" not in rendered
    assert "eth0,wlan0" not in rendered
    assert rendered.count("host-name=") == 1
    assert rendered.count("allow-interfaces=") == 1
    assert "[publish]" in rendered


def test_apply_avahi_pin_writes_data_config(tmp_path):
    path = tmp_path / "avahi-daemon.conf"
    run = _fake_ip(
        {
            ("ip", "-4", "route", "show", "default"): (
                "default via 192.168.1.1 dev wlan0 proto dhcp\n"
            ),
            (
                "ip",
                "-4",
                "-o",
                "addr",
                "show",
                "dev",
                "wlan0",
                "scope",
                "global",
            ): "3: wlan0 inet 192.168.1.245/24 brd 192.168.1.255 scope global wlan0\n",
        }
    )

    changed, interface = avahi_pin.apply_avahi_pin(config_path=path, run=run)

    assert changed is True
    assert interface == "wlan0"
    assert "host-name=rpi-divinu\n" in path.read_text(encoding="utf-8")
    assert "allow-interfaces=wlan0\n" in path.read_text(encoding="utf-8")


def test_avahi_unit_drop_in_uses_generated_data_config():
    drop_in = REPO_ROOT / "app" / "server" / "config" / "avahi-daemon-home-monitor.conf"
    text = drop_in.read_text(encoding="utf-8")

    assert "Requires=monitor-avahi-pin.service" in text
    assert "After=monitor-avahi-pin.service" in text
    assert (
        "ExecStart=/usr/sbin/avahi-daemon -s -f /data/config/avahi-daemon.conf" in text
    )


def test_monitor_server_recipe_installs_avahi_pin_unit():
    recipe = (
        REPO_ROOT
        / "meta-home-monitor"
        / "recipes-monitor"
        / "monitor-server"
        / "monitor-server_1.0.bb"
    )
    text = recipe.read_text(encoding="utf-8")

    assert "file://config/monitor-avahi-pin.service" in text
    assert "file://config/avahi-daemon-home-monitor.conf" in text
    assert "monitor-avahi-pin.service monitor.service" in text
    assert "avahi-daemon.service.d/10-home-monitor.conf" in text
