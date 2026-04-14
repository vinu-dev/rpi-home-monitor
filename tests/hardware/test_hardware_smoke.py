"""Pytest entrypoints for hardware-in-the-loop validation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _require_env(*names: str) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        pytest.skip(f"missing hardware environment variables: {', '.join(missing)}")


@pytest.mark.hardware
def test_server_and_camera_smoke_script():
    _require_env("HIL_SERVER", "HIL_SERVER_PASSWORD")
    cmd = [
        "bash",
        str(REPO_ROOT / "scripts" / "smoke-test.sh"),
        os.environ["HIL_SERVER"],
        os.environ["HIL_SERVER_PASSWORD"],
    ]
    if os.environ.get("HIL_CAMERA"):
        cmd.append(os.environ["HIL_CAMERA"])
    if os.environ.get("HIL_CAMERA_PASSWORD"):
        cmd.append(os.environ["HIL_CAMERA_PASSWORD"])
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)


@pytest.mark.hardware
@pytest.mark.ota
def test_end_to_end_pairing_and_recording_flow():
    _require_env(
        "HIL_SERVER",
        "HIL_CAMERA",
        "HIL_SERVER_PASSWORD",
        "WIFI_SSID",
        "WIFI_PASSWORD",
    )
    env = os.environ.copy()
    subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "e2e-smoke-test.sh"),
            env["HIL_SERVER"],
            env["HIL_CAMERA"],
            env["HIL_SERVER_PASSWORD"],
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )


@pytest.mark.hardware
def test_server_services_are_healthy_over_ssh(server_target):
    monitor = server_target.ssh("systemctl is-active monitor").stdout.strip()
    mediamtx = server_target.ssh("systemctl is-active mediamtx").stdout.strip()
    assert monitor == "active"
    assert mediamtx == "active"


@pytest.mark.hardware
def test_server_runtime_layout_exists_over_ssh(server_target):
    result = server_target.ssh(
        "test -d /data/config && test -d /data/recordings && test -d /data/live && test -d /data/certs",
    )
    assert result.returncode == 0


@pytest.mark.hardware
def test_camera_service_is_healthy_over_ssh(camera_target):
    camera_streamer = camera_target.ssh(
        "systemctl is-active camera-streamer"
    ).stdout.strip()
    assert camera_streamer == "active"


@pytest.mark.hardware
def test_camera_runtime_layout_exists_over_ssh(camera_target):
    result = camera_target.ssh(
        "test -d /data/config && test -d /data/certs && test -d /data/logs",
    )
    assert result.returncode == 0


@pytest.mark.hardware
def test_server_api_is_reachable_from_runner(wait_for_http):
    _require_env("HIL_SERVER")
    wait_for_http(f"https://{os.environ['HIL_SERVER']}/")


@pytest.mark.hardware
def test_camera_api_is_reachable_from_runner(wait_for_http):
    _require_env("HIL_CAMERA")
    wait_for_http(f"https://{os.environ['HIL_CAMERA']}/")
