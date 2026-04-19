"""
Shared fixtures and collection rules for layered camera-streamer tests."""

from pathlib import Path

import pytest

from camera_streamer.config import ConfigManager

LAYER_MARKERS = {
    "unit": "unit",
    "integration": "integration",
    "contracts": "contract",
    "security": "security",
}


def _required_marker(node_path: Path) -> str | None:
    parts = node_path.parts
    if "tests" not in parts:
        return None
    tests_index = parts.index("tests")
    if tests_index + 1 >= len(parts):
        return None
    return LAYER_MARKERS.get(parts[tests_index + 1])


def pytest_collection_modifyitems(config, items):
    """Enforce that camera tests live in an explicit layer directory."""
    for item in items:
        marker = _required_marker(Path(str(item.fspath)))
        if marker is None:
            continue

        marker_names = {mark.name for mark in item.iter_markers()}
        if marker not in marker_names:
            item.add_marker(getattr(pytest.mark, marker))

        conflicting = (
            set(LAYER_MARKERS.values())
            .difference({"security", marker})
            .intersection(marker_names)
        )
        if conflicting:
            raise pytest.UsageError(
                f"{item.nodeid} has conflicting layer markers: {sorted(conflicting)}"
            )


@pytest.fixture(autouse=True)
def _camera_skip_mount_check(monkeypatch):
    """Bypass ConfigManager's /data-is-mounted guard in unit tests.

    Production refuses to write default camera.conf unless /data is a
    real mountpoint distinct from / (ADR-0008 persistence contract).
    Tests use pytest's tmp_path, which always shares a device with /,
    so we flip the escape hatch env var for the whole camera suite.
    Individual tests that want to exercise the guard set it to "0".
    """
    monkeypatch.setenv("CAMERA_SKIP_MOUNT_CHECK", "1")


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


@pytest.fixture
def status_session_token():
    """Create a valid status-server session token, clean up after the test.

    Use this in tests that exercise code paths requiring authentication
    without going through a real HTTP login.  The token is injected
    directly into the in-memory session store.
    """
    from camera_streamer.status_server import (
        _create_session,
        _destroy_session,
        _session_lock,
        _sessions,
    )

    with _session_lock:
        _sessions.clear()
    token = _create_session()
    yield token
    _destroy_session(token)
    with _session_lock:
        _sessions.clear()


@pytest.fixture
def wifi_session_token():
    """Create a valid wifi-setup-server session token, clean up after the test."""
    from camera_streamer.wifi_setup import (
        _create_session,
        _destroy_session,
        _session_lock,
        _sessions,
    )

    with _session_lock:
        _sessions.clear()
    token = _create_session()
    yield token
    _destroy_session(token)
    with _session_lock:
        _sessions.clear()
