"""Launch a local HTTPS camera status server for browser and contract testing."""

from __future__ import annotations

import argparse
import signal
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CAMERA_APP_ROOT = REPO_ROOT / "app" / "camera"
if str(CAMERA_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(CAMERA_APP_ROOT))

from camera_streamer.config import ConfigManager
from camera_streamer.status_server import CameraStatusServer
import camera_streamer.status_server as status_server_module


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5444)
    parser.add_argument("--data-dir", default="")
    args = parser.parse_args()

    data_dir = Path(args.data_dir or tempfile.mkdtemp(prefix="hm-camera-"))
    cfg = _seed_config(data_dir)
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
