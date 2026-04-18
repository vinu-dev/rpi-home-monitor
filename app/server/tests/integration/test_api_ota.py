"""Tests for the OTA update API."""

import io

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
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "admin",
            "password": "pass",
        },
    )
    client.environ_base["HTTP_X_CSRF_TOKEN"] = response.get_json()["csrf_token"]


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
        assert "staged" in response.get_json()["message"].lower()

    def test_upload_sets_status(self, app, client):
        _login(app, client)
        data = {"file": (io.BytesIO(b"fake-swu-content"), "update.swu")}
        client.post(
            "/api/v1/ota/server/upload", data=data, content_type="multipart/form-data"
        )
        status = app.ota_service.get_status("server")
        assert status["state"] == "staged"


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

    def test_push_refuses_without_staged_bundle(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        response = client.post("/api/v1/ota/camera/cam-001/push")
        assert response.status_code == 409
        assert "upload" in response.get_json()["error"].lower()

    def test_pushes_update(self, app, client, monkeypatch):
        _login(app, client)
        _add_camera(app, "cam-001", "online")

        # Upload a bundle first (creates camera inbox).
        up = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"fake-swu-content"), "cam.swu")},
            content_type="multipart/form-data",
        )
        assert up.status_code == 200

        # Stub the background push so the test doesn't hit network.
        calls = []

        def _fake_push(ip, path, progress_cb=None, status_cb=None):
            calls.append((ip, path))
            if progress_cb:
                progress_cb(1, 1)
            if status_cb:
                status_cb("installed", 100)
            return True, "Installed"

        monkeypatch.setattr(app.camera_ota_client, "push_bundle", _fake_push)

        response = client.post("/api/v1/ota/camera/cam-001/push")
        assert response.status_code == 202

        # The push runs in a background thread — wait until it finished.
        import time

        for _ in range(40):
            if app.ota_service.get_status("cam-001")["state"] in {
                "installed",
                "error",
            }:
                break
            time.sleep(0.05)

        assert calls and calls[0][0] == "192.168.1.50"
        assert app.ota_service.get_status("cam-001")["state"] == "installed"

    def test_push_logs_audit(self, app, client, monkeypatch):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"x"), "cam.swu")},
            content_type="multipart/form-data",
        )
        monkeypatch.setattr(
            app.camera_ota_client,
            "push_bundle",
            lambda ip, path, progress_cb=None: (True, "ok"),
        )
        client.post("/api/v1/ota/camera/cam-001/push")
        events = app.audit.get_events(event_type="OTA_CAMERA_PUSH")
        assert len(events) >= 1


class TestCameraUpload:
    """Test POST /api/v1/ota/camera/<id>/upload."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/ota/camera/cam-001/upload")
        assert resp.status_code == 403

    def test_camera_not_found(self, app, client):
        _login(app, client)
        resp = client.post(
            "/api/v1/ota/camera/missing/upload",
            data={"file": (io.BytesIO(b"x"), "x.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 404

    def test_rejects_non_swu(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"x"), "x.zip")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_uploads_and_stages(self, app, client):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        resp = client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"payload"), "v2.swu")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["filename"] == "v2.swu"
        assert data["size"] == len(b"payload")

        # Status should now show it as staged + filename surfaces to status.
        status = client.get("/api/v1/ota/status").get_json()
        cam_entries = [c for c in status["cameras"] if c["id"] == "cam-001"]
        assert cam_entries and cam_entries[0]["staged_filename"] == "v2.swu"

    def test_upload_replaces_previous(self, app, client):
        """Only one bundle per camera at a time (prevents stale pushes)."""
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"old"), "old.swu")},
            content_type="multipart/form-data",
        )
        client.post(
            "/api/v1/ota/camera/cam-001/upload",
            data={"file": (io.BytesIO(b"new-body"), "new.swu")},
            content_type="multipart/form-data",
        )
        status = client.get("/api/v1/ota/status").get_json()
        cam = next(c for c in status["cameras"] if c["id"] == "cam-001")
        assert cam["staged_filename"] == "new.swu"


class TestCameraLiveStatus:
    """Test GET /api/v1/ota/camera/<id>/live-status."""

    def test_camera_not_found(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/ota/camera/missing/live-status")
        assert resp.status_code == 404

    def test_returns_unreachable_on_error(self, app, client, monkeypatch):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        monkeypatch.setattr(
            app.camera_ota_client,
            "get_status",
            lambda ip: (None, "timeout"),
        )
        resp = client.get("/api/v1/ota/camera/cam-001/live-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reachable"] is False
        assert data["error"] == "timeout"

    def test_proxies_camera_status(self, app, client, monkeypatch):
        _login(app, client)
        _add_camera(app, "cam-001", "online")
        monkeypatch.setattr(
            app.camera_ota_client,
            "get_status",
            lambda ip: ({"state": "installing", "progress": 70, "error": ""}, ""),
        )
        resp = client.get("/api/v1/ota/camera/cam-001/live-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reachable"] is True
        assert data["state"] == "installing"
        assert data["progress"] == 70
