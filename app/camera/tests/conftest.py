"""
Shared test fixtures for the camera-streamer test suite.

Provides temporary data directories and mock configurations
that mirror the production /data layout on the Zero 2W.
"""
import os
import pytest

from camera_streamer.config import ConfigManager


@pytest.fixture
def data_dir(tmp_path):
    """Create a temporary /data directory structure for the camera."""
    dirs = ["config", "certs", "logs"]
    for d in dirs:
        (tmp_path / d).mkdir()
    return tmp_path


@pytest.fixture
def camera_config(data_dir):
    """Write a sample camera.conf file and return ConfigManager."""
    config_file = data_dir / "config" / "camera.conf"
    config_file.write_text(
        "SERVER_IP=192.168.1.100\n"
        "SERVER_PORT=8554\n"
        "STREAM_NAME=stream\n"
        "WIDTH=1920\n"
        "HEIGHT=1080\n"
        "FPS=25\n"
        "CAMERA_ID=cam-test001\n"
    )
    mgr = ConfigManager(data_dir=str(data_dir))
    mgr.load()
    return mgr


@pytest.fixture
def camera_config_file(data_dir):
    """Write a sample camera.conf file and return the path."""
    config_file = data_dir / "config" / "camera.conf"
    config_file.write_text(
        "SERVER_IP=192.168.1.100\n"
        "SERVER_PORT=8554\n"
        "STREAM_NAME=stream\n"
        "WIDTH=1920\n"
        "HEIGHT=1080\n"
        "FPS=25\n"
        "CAMERA_ID=cam-test001\n"
    )
    return config_file


@pytest.fixture
def unconfigured_config(data_dir):
    """Return a ConfigManager with no server IP (needs setup)."""
    mgr = ConfigManager(data_dir=str(data_dir))
    mgr.load()
    return mgr


@pytest.fixture
def certs_dir(data_dir):
    """Create mock certificate files."""
    certs = data_dir / "certs"
    (certs / "client.crt").write_text("MOCK CERT")
    (certs / "client.key").write_text("MOCK KEY")
    (certs / "ca.crt").write_text("MOCK CA")
    return certs
