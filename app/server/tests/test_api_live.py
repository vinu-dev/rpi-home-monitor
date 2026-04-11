"""Tests for the live streaming API."""

import os

from monitor.auth import hash_password
from monitor.models import Camera


def _login(app, client, role="admin"):
    """Helper: create admin user and login."""
    from monitor.models import User

    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash=hash_password("pass"),
            role=role,
        )
    )
    client.post(
        "/api/v1/auth/login",
        json={
            "username": "admin",
            "password": "pass",
        },
    )


def _add_camera(app, camera_id="cam-001", status="online"):
    """Helper: add camera."""
    app.store.save_camera(
        Camera(id=camera_id, name="Test", status=status, ip="192.168.1.50")
    )


def _make_playlist(app, camera_id):
    """Helper: create a fake HLS playlist."""
    live_dir = os.path.join(app.config["LIVE_DIR"], camera_id)
    os.makedirs(live_dir, exist_ok=True)
    path = os.path.join(live_dir, "stream.m3u8")
    with open(path, "w") as f:
        f.write("#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nsegment_001.ts\n")
    return path


def _make_snapshot(app, camera_id):
    """Helper: create a fake snapshot JPEG."""
    live_dir = os.path.join(app.config["LIVE_DIR"], camera_id)
    os.makedirs(live_dir, exist_ok=True)
    path = os.path.join(live_dir, "snapshot.jpg")
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # fake JPEG header
    return path


class TestHLSPlaylist:
    """Test GET /api/v1/live/<cam-id>/stream.m3u8."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/live/cam-001/stream.m3u8").status_code == 401

    def test_camera_not_found(self, app, client):
        _login(app, client)
        assert client.get("/api/v1/live/cam-xxx/stream.m3u8").status_code == 404

    def test_camera_offline(self, app, client):
        _login(app, client)
        _add_camera(app, status="offline")
        response = client.get("/api/v1/live/cam-001/stream.m3u8")
        assert response.status_code == 503

    def test_no_playlist_file(self, app, client):
        _login(app, client)
        _add_camera(app)
        response = client.get("/api/v1/live/cam-001/stream.m3u8")
        assert response.status_code == 503

    def test_serves_playlist(self, app, client):
        _login(app, client)
        _add_camera(app)
        _make_playlist(app, "cam-001")
        response = client.get("/api/v1/live/cam-001/stream.m3u8")
        assert response.status_code == 200
        assert b"#EXTM3U" in response.data


class TestSnapshot:
    """Test GET /api/v1/live/<cam-id>/snapshot."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/live/cam-001/snapshot").status_code == 401

    def test_camera_not_found(self, app, client):
        _login(app, client)
        assert client.get("/api/v1/live/cam-xxx/snapshot").status_code == 404

    def test_camera_offline(self, app, client):
        _login(app, client)
        _add_camera(app, status="offline")
        assert client.get("/api/v1/live/cam-001/snapshot").status_code == 503

    def test_no_snapshot_file(self, app, client):
        _login(app, client)
        _add_camera(app)
        assert client.get("/api/v1/live/cam-001/snapshot").status_code == 503

    def test_serves_snapshot(self, app, client):
        _login(app, client)
        _add_camera(app)
        _make_snapshot(app, "cam-001")
        response = client.get("/api/v1/live/cam-001/snapshot")
        assert response.status_code == 200
        assert response.content_type == "image/jpeg"
