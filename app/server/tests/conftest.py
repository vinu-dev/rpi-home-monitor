"""
Shared test fixtures for the monitor-server test suite.

Provides a configured Flask test app, test client, and temporary
data directories that mirror the production /data layout.
"""

import json

import pytest

from monitor import create_app
from monitor.models import Camera, Clip, Settings, User


@pytest.fixture
def data_dir(tmp_path):
    """Create a temporary /data directory structure."""
    dirs = ["config", "recordings", "live", "certs", "logs"]
    for d in dirs:
        (tmp_path / d).mkdir()
    return tmp_path


@pytest.fixture
def app(data_dir):
    """Create a Flask test application with temporary data dirs."""
    app = create_app(
        config={
            "TESTING": True,
            "DATA_DIR": str(data_dir),
            "RECORDINGS_DIR": str(data_dir / "recordings"),
            "LIVE_DIR": str(data_dir / "live"),
            "CONFIG_DIR": str(data_dir / "config"),
            "CERTS_DIR": str(data_dir / "certs"),
            "SECRET_KEY": "test-secret-key-do-not-use-in-prod",
            "CLIP_DURATION_SECONDS": 180,
            "STORAGE_THRESHOLD_PERCENT": 90,
            "SESSION_TIMEOUT_MINUTES": 30,
        }
    )
    return app


@pytest.fixture
def client(app):
    """Flask test client — use this to make HTTP requests."""
    return app.test_client()


@pytest.fixture
def app_context(app):
    """Push an application context for tests that need it."""
    with app.app_context() as ctx:
        yield ctx


@pytest.fixture
def sample_camera():
    """A sample Camera dataclass instance."""
    return Camera(
        id="cam-abc123",
        name="Front Door",
        location="Outdoor",
        status="online",
        ip="192.168.1.50",
        rtsp_url="rtsps://192.168.1.50:8554/stream",
        recording_mode="continuous",
        resolution="1080p",
        fps=25,
        paired_at="2026-04-09T10:00:00Z",
        last_seen="2026-04-09T14:30:00Z",
        firmware_version="1.0.0",
        cert_serial="ABCDEF123456",
    )


@pytest.fixture
def sample_user():
    """A sample User dataclass instance."""
    return User(
        id="user-001",
        username="admin",
        password_hash="$2b$12$fakehashfortest",
        role="admin",
        created_at="2026-04-09T10:00:00Z",
        last_login="2026-04-09T14:00:00Z",
    )


@pytest.fixture
def sample_settings():
    """A sample Settings dataclass instance."""
    return Settings()


@pytest.fixture
def sample_clip():
    """A sample Clip dataclass instance."""
    return Clip(
        camera_id="cam-abc123",
        filename="14-30-00.mp4",
        date="2026-04-09",
        start_time="14:30:00",
        duration_seconds=180,
        size_bytes=52428800,
        thumbnail="14-30-00.thumb.jpg",
    )


@pytest.fixture
def cameras_json(data_dir, sample_camera):
    """Write a cameras.json file with one sample camera."""
    from dataclasses import asdict

    cameras_file = data_dir / "config" / "cameras.json"
    cameras_file.write_text(json.dumps([asdict(sample_camera)], indent=2))
    return cameras_file


@pytest.fixture
def users_json(data_dir, sample_user):
    """Write a users.json file with one sample user."""
    from dataclasses import asdict

    users_file = data_dir / "config" / "users.json"
    users_file.write_text(json.dumps([asdict(sample_user)], indent=2))
    return users_file


@pytest.fixture
def settings_json(data_dir, sample_settings):
    """Write a settings.json file with defaults."""
    from dataclasses import asdict

    settings_file = data_dir / "config" / "settings.json"
    settings_file.write_text(json.dumps(asdict(sample_settings), indent=2))
    return settings_file
