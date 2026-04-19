"""
Shared fixtures and collection rules for layered monitor-server tests."""

import json
from pathlib import Path

import pytest

from monitor import create_app
from monitor.auth import hash_password
from monitor.models import Camera, Clip, Settings, User

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
    """Enforce that server tests live in an explicit layer directory."""
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
            "SESSION_COOKIE_SECURE": False,
        }
    )
    return app


@pytest.fixture
def client(app):
    """Flask test client — use this to make HTTP requests."""
    return app.test_client()


@pytest.fixture
def logged_in_client(app, client):
    """Factory fixture: returns a callable that logs in and returns the client.

    Usage::

        def test_something(logged_in_client):
            client = logged_in_client()          # admin, default credentials
            client = logged_in_client("viewer")  # viewer role

    The client has the CSRF token pre-set in environ_base so all
    state-changing requests pass CSRF validation automatically.
    """

    def _login(role="admin", username=None, password="pass"):
        uname = username if username is not None else role
        app.store.save_user(
            User(
                id=f"user-{uname}",
                username=uname,
                password_hash=hash_password(password),
                role=role,
                created_at="2026-01-01T00:00:00Z",
            )
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": uname, "password": password},
        )
        client.environ_base["HTTP_X_CSRF_TOKEN"] = resp.get_json()["csrf_token"]
        return client

    return _login


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
