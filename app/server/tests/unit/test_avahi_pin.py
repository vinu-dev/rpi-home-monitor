# REQ: SWR-024, SWR-050; RISK: RISK-010, RISK-012; SEC: SC-012; TEST: TC-023
"""Tests for the server mDNS identity pinning helper."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    assert avahi_pin.choose_publish_interfaces(run=run) == ("eth0",)


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
    assert avahi_pin.choose_publish_interfaces(run=run) == ("eth0",)


def test_choose_publish_interface_falls_back_to_wifi_when_ethernet_has_no_ip():
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
                "eth0",
                "scope",
                "global",
            ): "",
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

    assert avahi_pin.choose_publish_interfaces(run=run) == ("wlan0",)


def test_choose_publish_interface_falls_back_to_existing_interface():
    run = _fake_ip(
        {
            ("ip", "-4", "route", "show", "default"): "default dev tailscale0\n",
            ("ip", "link", "show", "dev", "eth0"): "2: eth0: <BROADCAST>\n",
        }
    )

    assert avahi_pin.choose_publish_interface(run=run) == "eth0"


def test_choose_publish_interfaces_adds_physical_default_when_preferred_missing():
    run = _fake_ip(
        {
            ("ip", "-4", "route", "show", "default"): "default dev enp2s0\n",
        }
    )

    assert avahi_pin.choose_publish_interfaces(run=run) == ("enp2s0",)


def test_choose_publish_interfaces_adds_active_nonstandard_lan_interface():
    run = _fake_ip(
        {
            ("ip", "-4", "route", "show", "default"): "default dev tailscale0\n",
            ("ip", "-4", "-o", "addr", "show", "scope", "global"): (
                "2: enp2s0 inet 192.168.1.244/24 brd 192.168.1.255 scope global enp2s0\n"
                "3: tailscale0 inet 100.64.0.1/32 scope global tailscale0\n"
            ),
        }
    )

    assert avahi_pin.choose_publish_interfaces(run=run) == ("enp2s0",)


def test_choose_publish_interface_uses_first_candidate_as_last_resort():
    run = _fake_ip({})

    assert (
        avahi_pin.choose_publish_interface(preferred=("lan0", "lan1"), run=run)
        == "lan0"
    )


def test_preferred_interfaces_can_come_from_env(monkeypatch):
    monkeypatch.setenv("MONITOR_AVAHI_PREFERRED_INTERFACES", "lan0, wlan0")

    assert avahi_pin._preferred_interfaces_from_env() == ("lan0", "wlan0")


def test_render_avahi_config_pins_server_section_without_duplicate_keys():
    existing = """[server]
#host-name=old-name
allow-interfaces=eth0,wlan0
use-ipv4=yes

[publish]
publish-addresses=yes
"""

    rendered = avahi_pin.render_avahi_config(
        existing, "rpi-divinu.local", ("eth0", "wlan0")
    )

    assert "host-name=rpi-divinu\n" in rendered
    assert "allow-interfaces=eth0,wlan0\n" in rendered
    assert "old-name" not in rendered
    assert rendered.count("host-name=") == 1
    assert rendered.count("allow-interfaces=") == 1
    assert "[publish]" in rendered


def test_render_avahi_config_creates_server_section_when_missing():
    rendered = avahi_pin.render_avahi_config(
        "[publish]\npublish-addresses=yes\n", "rpi-divinu", ""
    )

    assert rendered.startswith("[server]\nhost-name=rpi-divinu\nuse-ipv4=yes")
    assert "allow-interfaces" not in rendered


@pytest.mark.parametrize(
    ("host_name", "interface", "message"),
    [
        ("bad host", "eth0", "host name"),
        ("rpi-divinu", "eth0;rm", "interface"),
    ],
)
def test_render_avahi_config_rejects_invalid_values(host_name, interface, message):
    with pytest.raises(ValueError, match=message):
        avahi_pin.render_avahi_config("", host_name, interface)


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


def test_apply_avahi_pin_removes_noncanonical_wifi_pin(tmp_path):
    path = tmp_path / "avahi-daemon.conf"
    path.write_text(
        "[server]\nhost-name=rpi-divinu\nallow-interfaces=eth0,wlan0\n",
        encoding="utf-8",
    )
    run = _fake_ip(
        {
            ("ip", "-4", "route", "show", "default"): "default dev eth0\n",
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
            ): "2: eth0 inet 192.168.1.244/24 scope global eth0\n",
        }
    )

    changed, interface = avahi_pin.apply_avahi_pin(config_path=path, run=run)

    assert changed is True
    assert interface == "eth0"
    assert "allow-interfaces=eth0\n" in path.read_text(encoding="utf-8")
    assert "allow-interfaces=eth0,wlan0\n" not in path.read_text(encoding="utf-8")


def test_apply_avahi_pin_reports_already_pinned_for_canonical_lan_interface(tmp_path):
    path = tmp_path / "avahi-daemon.conf"
    path.write_text(
        "[server]\nhost-name=rpi-divinu\nallow-interfaces=eth0\n",
        encoding="utf-8",
    )
    run = _fake_ip({})

    changed, interface = avahi_pin.apply_avahi_pin(config_path=path, run=run)

    assert changed is False
    assert interface == "eth0"


def test_write_if_changed_preserves_original_once(tmp_path):
    path = tmp_path / "avahi-daemon.conf"
    path.write_text("old\n", encoding="utf-8")

    assert avahi_pin.write_if_changed(path, "new\n") is True
    assert path.with_suffix(".conf.pre-monitor").read_text(encoding="utf-8") == "old\n"
    assert avahi_pin.write_if_changed(path, "newer\n") is True
    assert path.with_suffix(".conf.pre-monitor").read_text(encoding="utf-8") == "old\n"


def test_command_output_handles_failures():
    with patch.object(avahi_pin.subprocess, "run", side_effect=FileNotFoundError):
        assert avahi_pin._command_output(["ip"]) == ""
    result = MagicMock(returncode=1, stderr="boom", stdout="")
    with patch.object(avahi_pin.subprocess, "run", return_value=result):
        assert avahi_pin._command_output(["ip"]) == ""


def test_command_output_returns_stdout_on_success():
    result = MagicMock(returncode=0, stdout="default dev eth0\n")

    with patch.object(avahi_pin.subprocess, "run", return_value=result):
        assert avahi_pin._command_output(["ip"]) == "default dev eth0\n"


def test_blank_interface_checks_return_false():
    assert avahi_pin._interface_has_ipv4("") is False
    assert avahi_pin._interface_exists("") is False


def test_main_logs_pinned_identity():
    with patch.object(avahi_pin, "apply_avahi_pin", return_value=(False, "eth0")):
        assert avahi_pin.main() == 0


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
    services = next(
        line for line in text.splitlines() if line.startswith("SYSTEMD_SERVICE:${PN}")
    )

    assert "file://config/monitor-avahi-pin.service" in text
    assert "file://config/avahi-daemon-home-monitor.conf" in text
    assert "file://config/monitor-network-dispatcher.sh" in text
    assert "monitor-avahi-pin.service" in services
    assert "monitor.service" in services
    assert "avahi-daemon.service.d/10-home-monitor.conf" in text
    assert "NetworkManager/dispatcher.d/20-home-monitor-lan-identity" in text


def test_monitor_network_dispatcher_reapplies_avahi_identity():
    script = (
        REPO_ROOT / "app" / "server" / "config" / "monitor-network-dispatcher.sh"
    ).read_text(encoding="utf-8")

    assert "monitor.services.avahi_pin" in script
    assert "try-restart avahi-daemon.service" in script
    assert "dhcp4-change" in script
