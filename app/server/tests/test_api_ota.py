"""Tests for the OTA update API."""

import io

from monitor.api.ota import _ota_status
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


class TestOTAStatus:
    """Test GET /api/v1/ota/status."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/ota/status").status_code == 401

    def test_returns_status(self, app, client):
        _login(app, client)
        response = client.get("/api/v1/ota/status")
        assert response.status_code == 200
        data = response.get_json()
        assert "server" in data
        assert data["server"]["current_version"] == "1.0.0"
        assert "cameras" in data

    def test_includes_camera_status(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        data = client.get("/api/v1/ota/status").get_json()
        assert len(data["cameras"]) == 1
        assert data["cameras"][0]["id"] == "cam-001"

    def test_excludes_pending_cameras(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "pending")
        data = client.get("/api/v1/ota/status").get_json()
        assert len(data["cameras"]) == 0


class TestServerUpload:
    """Test POST /api/v1/ota/server/upload."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        response = client.post("/api/v1/ota/server/upload")
        assert response.status_code == 403

    def test_requires_file(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/ota/server/upload")
        assert response.status_code == 400

    def test_rejects_non_swu(self, app, client):
        _login(app, client)
        data = {"file": (io.BytesIO(b"data"), "update.zip")}
        response = client.post(
            "/api/v1/ota/server/upload", data=data, content_type="multipart/form-data"
        )
        assert response.status_code == 400
        assert "swu" in response.get_json()["error"].lower()

    def test_uploads_swu(self, app, client):
        _login(app, client)
        data = {"file": (io.BytesIO(b"fake-swu-content"), "update.swu")}
        response = client.post(
            "/api/v1/ota/server/upload", data=data, content_type="multipart/form-data"
        )
        assert response.status_code == 200
        assert response.get_json()["message"] == "Update image staged"

    def test_upload_logs_audit(self, app, client):
        _login(app, client)
        data = {"file": (io.BytesIO(b"fake-swu-content"), "update.swu")}
        client.post(
            "/api/v1/ota/server/upload", data=data, content_type="multipart/form-data"
        )
        events = app.audit.get_events(event_type="OTA_UPLOADED")
        assert len(events) >= 1


class TestCameraPush:
    """Test POST /api/v1/ota/camera/<id>/push."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        assert client.post("/api/v1/ota/camera/cam-001/push").status_code == 403

    def test_camera_not_found(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/ota/camera/cam-xxx/push")
        assert response.status_code == 404

    def test_camera_must_be_online(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "offline")
        response = client.post("/api/v1/ota/camera/cam-001/push")
        assert response.status_code == 400

    def test_pushes_update(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.post(
            "/api/v1/ota/camera/cam-001/push", json={"version": "1.1.0"}
        )
        assert response.status_code == 200
        assert "cam-001" in _ota_status
        assert _ota_status["cam-001"]["state"] == "pending"

    def test_push_logs_audit(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        client.post("/api/v1/ota/camera/cam-001/push")
        events = app.audit.get_events(event_type="OTA_CAMERA_PUSH")
        assert len(events) >= 1
