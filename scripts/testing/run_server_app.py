"""Launch a local HTTPS server instance for browser and contract testing."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_APP_ROOT = REPO_ROOT / "app" / "server"
if str(SERVER_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_APP_ROOT))

from monitor import create_app
from monitor.auth import hash_password
from monitor.models import Camera, Settings, User


def _ensure_dirs(data_dir: Path) -> None:
    for name in ("config", "recordings", "live", "certs", "logs"):
        (data_dir / name).mkdir(parents=True, exist_ok=True)


def _seed_app(app, data_dir: Path) -> None:
    (data_dir / ".setup-done").write_text("done\n", encoding="utf-8")
    app.store.save_settings(
        Settings(
            hostname="127.0.0.1",
            firmware_version="test-build",
            timezone="UTC",
        )
    )
    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash=hash_password("pass1234"),
            role="admin",
            must_change_password=False,
        )
    )
    app.store.save_camera(
        Camera(
            id="cam-001",
            name="Front Door",
            location="Outdoor",
            status="online",
            ip="192.168.1.50",
            recording_mode="continuous",
            resolution="1080p",
            fps=25,
            firmware_version="cam-test-build",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5443)
    parser.add_argument("--mode", choices=("setup", "seeded"), default="seeded")
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()

    data_dir = Path(args.data_dir or tempfile.mkdtemp(prefix="hm-server-"))
    _ensure_dirs(data_dir)

    app = create_app(
        config={
            "TESTING": True,
            "DATA_DIR": str(data_dir),
            "RECORDINGS_DIR": str(data_dir / "recordings"),
            "LIVE_DIR": str(data_dir / "live"),
            "CONFIG_DIR": str(data_dir / "config"),
            "CERTS_DIR": str(data_dir / "certs"),
            "SECRET_KEY": "playwright-secret-key",
            "SESSION_TIMEOUT_MINUTES": 30,
        }
    )

    if args.mode == "seeded":
        _seed_app(app, data_dir)

    app.run(host=args.host, port=args.port, ssl_context="adhoc", use_reloader=False)


if __name__ == "__main__":
    main()
