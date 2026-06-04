# REQ: SWR-001, SWR-034, SWR-046; RISK: RISK-002, RISK-019; SEC: SC-001, SC-017, SC-018; TEST: TC-032, TC-043
"""Packaging guards for hybrid password + certificate admin login wiring."""

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
    assert "file://config/bootstrap-gui-cert.sh" in recipe
    assert "file://config/home-monitor-gui-cert-bootstrap.service" in recipe
    assert "home-monitor-gui-cert-bootstrap.service" in recipe


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


def test_nginx_uses_separate_gui_certificate_without_touching_mediamtx():
    nginx = _read("app/server/config/nginx-monitor.conf")
    mediamtx = _read("meta-home-monitor/recipes-multimedia/mediamtx/files/mediamtx.yml")

    assert "ssl_certificate     /data/certs/gui-server.crt;" in nginx
    assert "ssl_certificate_key /data/certs/gui-server.key;" in nginx
    assert "serverCert: /data/certs/server.crt" in mediamtx
    assert "serverKey: /data/certs/server.key" in mediamtx


def test_nginx_keeps_normal_gui_on_main_https_listener():
    nginx = _read("app/server/config/nginx-monitor.conf")

    assert "return 302 https://$host:9443$request_uri;" not in nginx
    assert "location ^~ /api/" in nginx
    assert "location / {\n        proxy_pass http://127.0.0.1:5000;" in nginx


def test_certificate_listener_only_handles_admin_certificate_login_exchange():
    nginx = _read("app/server/config/nginx-monitor.conf")

    cert_listener = nginx.split("listen 9443 ssl;", 1)[1]
    assert "location = /api/v1/auth/cert/session" in cert_listener
    assert "location = /login" in cert_listener
    assert "location ^~ /api/v1/setup/" in cert_listener
    assert "location / {\n        return 302 https://$host$request_uri;" in cert_listener
    assert "location ~ ^/live/" not in cert_listener
    assert "location ~ ^/clips/" not in cert_listener


def test_server_firewall_allows_certificate_gui_port():
    nft = _read("app/server/config/nftables-server.conf")

    assert "9443" in nft


def test_server_firewall_exposes_only_required_lan_product_ports():
    nft = _read("app/server/config/nftables-server.conf")

    assert "tcp dport { 80, 443, 9443 }" in nft
    assert 'iifname "tailscale0" tcp dport { 80, 443, 9443 }' in nft
    assert "tcp dport 8322" in nft
    assert "udp dport 8189" in nft
    assert 'iifname "tailscale0" udp dport 8189' in nft
    assert "udp dport 41641 accept" in nft
    assert "tcp dport 8554" not in nft
    assert "tcp dport 8889" not in nft
    assert "tcp dport 1935" not in nft
    assert "udp dport 8890" not in nft


def test_mediamtx_binds_internal_protocols_to_loopback_only():
    mediamtx = _read("meta-home-monitor/recipes-multimedia/mediamtx/files/mediamtx.yml")

    assert "rtspAddress: 127.0.0.1:8554" in mediamtx
    assert "rtspsAddress: :8322" in mediamtx
    assert "webrtcAddress: 127.0.0.1:8889" in mediamtx
    assert "webrtcLocalUDPAddress: :8189" in mediamtx
    assert "rtmp: no" in mediamtx
    assert "srt: no" in mediamtx


def test_server_image_enables_firewall_at_boot():
    recipe = _read(
        "meta-home-monitor/recipes-monitor/monitor-server/monitor-server_1.0.bb"
    )
    unit = _read("app/server/config/home-monitor-firewall.service")

    assert "file://config/home-monitor-firewall.service" in recipe
    assert "home-monitor-firewall.service" in recipe
    assert "${systemd_system_unitdir}/home-monitor-firewall.service" in recipe
    assert "ExecStart=/usr/sbin/nft -f /etc/nftables.d/monitor.conf" in unit
    assert (
        "Before=network-pre.target network.target nginx.service mediamtx.service monitor.service"
        in unit
    )


def test_packaged_monitor_defaults_to_hybrid_auth():
    unit = _read("app/server/config/monitor.service")

    assert "Environment=MONITOR_AUTH_MODE=mixed" in unit
    assert "Environment=MONITOR_CERT_AUTH_ALLOW_PROFILE_LOGIN=1" in unit
    assert "Environment=MONITOR_CERT_AUTH_ENFORCE_TIME=1" in unit
    assert "Environment=MONITOR_SETUP_CERT_REQUIRED=1" in unit


def test_rpi_repo_does_not_contain_ca_generator_project():
    assert not (REPO_ROOT / "scripts/certs/home_monitor_ca.py").exists()
    assert not (REPO_ROOT / "home_monitor_ca.py").exists()
