# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Launch a local HTTPS camera status server for browser and contract testing."""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMERA_APP_ROOT = REPO_ROOT / "app" / "camera"
if str(CAMERA_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(CAMERA_APP_ROOT))

import camera_streamer.status_server as status_server_module
from camera_streamer.config import ConfigManager
from camera_streamer.status_server import CameraStatusServer


def _seed_config(data_dir: Path) -> ConfigManager:
    for name in ("config", "certs", "logs"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)

    cfg = ConfigManager(data_dir=str(data_dir))
    cfg.update(
        server_ip="127.0.0.1",
        server_port=8322,
        stream_name="cam-001",
        camera_id="cam-001",
        admin_username="admin",
    )
    cfg.set_password("pass1234")
    cfg.save()
    cfg.load()
    return cfg


def _install_tls_fallback_when_openssl_is_missing(cfg: ConfigManager) -> None:
    """Let browser tests run on workstations that do not ship openssl.

    Production still uses ``camera_streamer.status_server._ensure_tls_material``.
    This helper only patches the local test launcher when OpenSSL is absent,
    matching the pytest fallback coverage for the status server.
    """
    if shutil.which("openssl"):
        return

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path_raw, key_path_raw = status_server_module._status_tls_paths(cfg)
    cert_path = Path(cert_path_raw)
    key_path = Path(key_path_raw)
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    if not cert_path.exists() or not key_path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=30))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        key_path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.chmod(0o600)

    status_server_module._ensure_tls_material = lambda _config: (
        str(cert_path),
        str(key_path),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5444)
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()

    data_dir = Path(args.data_dir or tempfile.mkdtemp(prefix="hm-camera-"))
    cfg = _seed_config(data_dir)
    _install_tls_fallback_when_openssl_is_missing(cfg)
    status_server_module.LISTEN_PORT = args.port
    server = CameraStatusServer(cfg)

    shutting_down = {"value": False}

    def _handle_signal(signum, frame):
        shutting_down["value"] = True
        server.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if not server.start():
        raise SystemExit("failed to start camera status server")

    while not shutting_down["value"]:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
