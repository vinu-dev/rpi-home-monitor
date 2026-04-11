"""Tests for the cameras API."""

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


def _add_camera(app, camera_id="cam-001", status="pending", name="", ip="192.168.1.50"):
    """Helper: add a camera to the store."""
    camera = Camera(id=camera_id, name=name, ip=ip, status=status)
    app.store.save_camera(camera)
    return camera


class TestListCameras:
    """Test GET /api/v1/cameras."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/cameras").status_code == 401

    def test_returns_empty_list(self, app, client):
        _login(app, client)
        data = client.get("/api/v1/cameras").get_json()
        assert data == []

    def test_returns_cameras(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online", "Front Door")
        _add_camera(app, "cam-002", "pending")
        data = client.get("/api/v1/cameras").get_json()
        assert len(data) == 2
        assert "password_hash" not in str(data)

    def test_viewer_can_list(self, app, client):
        _login(app, client, role="viewer")
        assert client.get("/api/v1/cameras").status_code == 200

    def test_camera_fields(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online", "Front Door")
        cam = client.get("/api/v1/cameras").get_json()[0]
        for field in [
            "id",
            "name",
            "location",
            "status",
            "ip",
            "recording_mode",
            "resolution",
            "fps",
            "paired_at",
            "last_seen",
            "firmware_version",
        ]:
            assert field in cam


class TestConfirmCamera:
    """Test POST /api/v1/cameras/<id>/confirm."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        assert client.post("/api/v1/cameras/cam-001/confirm").status_code == 403

    def test_confirms_pending_camera(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "pending", ip="192.168.1.50")
        response = client.post(
            "/api/v1/cameras/cam-001/confirm",
            json={
                "name": "Front Door",
                "location": "Outdoor",
            },
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["name"] == "Front Door"
        assert data["status"] == "online"
        assert data["paired_at"] is not None

    def test_confirm_sets_rtsp_url(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "pending", ip="192.168.1.50")
        client.post("/api/v1/cameras/cam-001/confirm")
        camera = app.store.get_camera("cam-001")
        assert camera.rtsp_url == "rtsp://127.0.0.1:8554/cam-001"

    def test_cannot_confirm_already_confirmed(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.post("/api/v1/cameras/cam-001/confirm")
        assert response.status_code == 400

    def test_confirm_nonexistent(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/cameras/cam-xxx/confirm")
        assert response.status_code == 404

    def test_confirm_with_default_name(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "pending")
        response = client.post("/api/v1/cameras/cam-001/confirm")
        assert response.status_code == 200
        assert response.get_json()["name"] == "cam-001"


class TestUpdateCamera:
    """Test PUT /api/v1/cameras/<id>."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        assert (
            client.put("/api/v1/cameras/cam-001", json={"name": "x"}).status_code == 403
        )

    def test_update_name(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"name": "Back Yard"})
        assert response.status_code == 200
        camera = app.store.get_camera("cam-001")
        assert camera.name == "Back Yard"

    def test_update_recording_mode(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"recording_mode": "off"})
        assert response.status_code == 200

    def test_invalid_recording_mode(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.put(
            "/api/v1/cameras/cam-001", json={"recording_mode": "magic"}
        )
        assert response.status_code == 400

    def test_invalid_resolution(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"resolution": "4k"})
        assert response.status_code == 400

    def test_invalid_fps(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"fps": 60})
        assert response.status_code == 400

    def test_unknown_fields_rejected(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"bogus": "val"})
        assert response.status_code == 400

    def test_requires_json(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001")
        assert response.status_code == 400

    def test_camera_not_found(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/cameras/cam-xxx", json={"name": "x"})
        assert response.status_code == 404


class TestDeleteCamera:
    """Test DELETE /api/v1/cameras/<id>."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        assert client.delete("/api/v1/cameras/cam-001").status_code == 403

    def test_deletes_camera(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.delete("/api/v1/cameras/cam-001")
        assert response.status_code == 200
        assert app.store.get_camera("cam-001") is None

    def test_delete_nonexistent(self, app, client):
        _login(app, client)
        response = client.delete("/api/v1/cameras/cam-xxx")
        assert response.status_code == 404


class TestCameraStatus:
    """Test GET /api/v1/cameras/<id>/status."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/cameras/cam-001/status").status_code == 401

    def test_returns_status(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online", "Front Door")
        data = client.get("/api/v1/cameras/cam-001/status").get_json()
        assert data["id"] == "cam-001"
        assert data["status"] == "online"

    def test_status_not_found(self, app, client):
        _login(app, client)
        response = client.get("/api/v1/cameras/cam-xxx/status")
        assert response.status_code == 404


class TestCamerasAuditLog:
    """Test audit logging for camera operations."""

    def test_confirm_logged(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "pending")
        client.post("/api/v1/cameras/cam-001/confirm", json={"name": "Front"})
        events = app.audit.get_events(event_type="CAMERA_CONFIRMED")
        assert len(events) >= 1

    def test_delete_logged(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        client.delete("/api/v1/cameras/cam-001")
        events = app.audit.get_events(event_type="CAMERA_DELETED")
        assert len(events) >= 1
