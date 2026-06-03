# REQ: SWR-001, SWR-034, SWR-046; RISK: RISK-002, RISK-019; SEC: SC-001, SC-017, SC-018; TEST: TC-032, TC-043
"""Packaging guards for certificate-only GUI login image wiring."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def _read(path):
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_provisioning_ca_inputs_are_gitignored():
    gitignore = _read(".gitignore")

    assert "local-secrets/" in gitignore
    assert "app/server/config/generated/" in gitignore


def test_stage_script_requires_external_public_ca_only():
    script = _read("scripts/stage-provisioning-ca.sh")

    assert "local-secrets/provisioning-ca/home-monitor-provisioning-ca.crt" in script
    assert (
        "app/server/config/generated/trust/home-monitor-provisioning-ca.crt" in script
    )
    assert "Do not copy CA private keys" in script
    assert "client .p12 bundles" in script


def test_server_image_packages_staged_public_ca_and_nginx_snippet():
    recipe = _read(
        "meta-home-monitor/recipes-monitor/monitor-server/monitor-server_1.0.bb"
    )

    assert "file://config/generated/trust/home-monitor-provisioning-ca.crt" in recipe
    assert "file://config/nginx-client-cert.d/provisioning-client-ca.conf" in recipe
    assert "${sysconfdir}/home-monitor/trust/home-monitor-provisioning-ca.crt" in recipe
    assert "${sysconfdir}/nginx/client-cert.d/provisioning-client-ca.conf" in recipe


def test_nginx_uses_baked_client_ca_trust_anchor():
    nginx = _read("app/server/config/nginx-monitor.conf")
    snippet = _read("app/server/config/nginx-client-cert.d/provisioning-client-ca.conf")

    assert "include /etc/nginx/client-cert.d/*.conf;" in nginx
    assert "include /data/config/nginx-client-cert.d/*.conf;" not in nginx
    assert "listen 443 ssl;" in nginx
    assert "listen 9443 ssl;" in nginx
    assert (
        "ssl_client_certificate /etc/home-monitor/trust/"
        "home-monitor-provisioning-ca.crt;"
    ) in snippet
    assert "ssl_verify_client on;" in snippet


def test_nginx_redirects_gui_pages_to_certificate_listener_without_moving_api():
    nginx = _read("app/server/config/nginx-monitor.conf")

    assert (
        "location ~ ^/(|login|dashboard|live|recordings|events|alerts|logs|settings|shares|help/network-fallback)/?$"
        in nginx
    )
    assert "return 302 https://$host:9443$request_uri;" in nginx
    assert nginx.index("location ~ ^/(|login|dashboard") < nginx.index(
        "location ^~ /api/"
    )


def test_server_firewall_allows_certificate_gui_port():
    nft = _read("app/server/config/nftables-server.conf")

    assert "9443" in nft


def test_packaged_monitor_defaults_to_certificate_auth():
    unit = _read("app/server/config/monitor.service")

    assert "Environment=MONITOR_AUTH_MODE=certificate" in unit
    assert "Environment=MONITOR_CERT_AUTH_ALLOW_PROFILE_LOGIN=1" in unit
    assert "Environment=MONITOR_CERT_AUTH_ENFORCE_TIME=1" in unit


def test_rpi_repo_does_not_contain_ca_generator_project():
    assert not (REPO_ROOT / "scripts/certs/home_monitor_ca.py").exists()
    assert not (REPO_ROOT / "home_monitor_ca.py").exists()
