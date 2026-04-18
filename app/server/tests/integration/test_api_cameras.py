"""Tests for the cameras API."""

from monitor.models import Camera


def _add_camera(app, camera_id="cam-001", status="pending", name="", ip="192.168.1.50"):
    """Helper: add a camera to the store."""
    camera = Camera(id=camera_id, name=name, ip=ip, status=status)
    app.store.save_camera(camera)
    return camera


class TestListCameras:
    """Test GET /api/v1/cameras."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/cameras").status_code == 401

    def test_returns_empty_list(self, logged_in_client):
        client = logged_in_client()
        data = client.get("/api/v1/cameras").get_json()
        assert data == []

    def test_returns_cameras(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online", "Front Door")
        _add_camera(app, "cam-002", "pending")
        data = client.get("/api/v1/cameras").get_json()
        assert len(data) == 2
        assert "password_hash" not in str(data)

    def test_viewer_can_list(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.get("/api/v1/cameras").status_code == 200

    def test_camera_fields(self, app, logged_in_client):
        client = logged_in_client()
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


class TestAddCamera:
    """Test POST /api/v1/cameras."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        resp = client.post("/api/v1/cameras", json={"id": "cam-new"})
        assert resp.status_code == 403

    def test_requires_auth(self, client):
        resp = client.post("/api/v1/cameras", json={"id": "cam-new"})
        assert resp.status_code == 401

    def test_adds_pending_camera(self, app, logged_in_client):
        client = logged_in_client()
        resp = client.post(
            "/api/v1/cameras",
            json={"id": "cam-new", "name": "Front Door", "location": "Outdoor"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["id"] == "cam-new"
        assert data["name"] == "Front Door"
        assert data["status"] == "pending"
        # Verify persisted
        cam = app.store.get_camera("cam-new")
        assert cam is not None
        assert cam.location == "Outdoor"

    def test_rejects_empty_id(self, logged_in_client):
        client = logged_in_client()
        resp = client.post("/api/v1/cameras", json={"id": ""})
        assert resp.status_code == 400

    def test_rejects_duplicate(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "pending")
        resp = client.post("/api/v1/cameras", json={"id": "cam-001"})
        assert resp.status_code == 409

    def test_defaults_name_to_id(self, logged_in_client):
        client = logged_in_client()
        resp = client.post("/api/v1/cameras", json={"id": "cam-abc"})
        assert resp.status_code == 201
        assert resp.get_json()["name"] == "cam-abc"


class TestConfirmCamera:
    """Test POST /api/v1/cameras/<id>/confirm."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.post("/api/v1/cameras/cam-001/confirm").status_code == 403

    def test_confirms_pending_camera(self, app, logged_in_client):
        client = logged_in_client()
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

    def test_confirm_sets_rtsp_url(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "pending", ip="192.168.1.50")
        client.post("/api/v1/cameras/cam-001/confirm")
        camera = app.store.get_camera("cam-001")
        assert camera.rtsp_url == "rtsp://127.0.0.1:8554/cam-001"

    def test_cannot_confirm_already_confirmed(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.post("/api/v1/cameras/cam-001/confirm")
        assert response.status_code == 200
        assert response.get_json()["status"] == "online"

    def test_confirm_nonexistent(self, logged_in_client):
        client = logged_in_client()
        response = client.post("/api/v1/cameras/cam-xxx/confirm")
        assert response.status_code == 404

    def test_confirm_with_default_name(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "pending")
        response = client.post("/api/v1/cameras/cam-001/confirm")
        assert response.status_code == 200
        assert response.get_json()["name"] == "cam-001"


class TestUpdateCamera:
    """Test PUT /api/v1/cameras/<id>."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert (
            client.put("/api/v1/cameras/cam-001", json={"name": "x"}).status_code == 403
        )

    def test_update_name(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"name": "Back Yard"})
        assert response.status_code == 200
        camera = app.store.get_camera("cam-001")
        assert camera.name == "Back Yard"

    def test_update_recording_mode(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"recording_mode": "off"})
        assert response.status_code == 200

    def test_invalid_recording_mode(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.put(
            "/api/v1/cameras/cam-001", json={"recording_mode": "magic"}
        )
        assert response.status_code == 400

    def test_invalid_resolution(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"resolution": "4k"})
        assert response.status_code == 400

    def test_invalid_fps(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"fps": 60})
        assert response.status_code == 400

    def test_unknown_fields_rejected(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001", json={"bogus": "val"})
        assert response.status_code == 400

    def test_requires_json(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.put("/api/v1/cameras/cam-001")
        assert response.status_code == 400

    def test_camera_not_found(self, logged_in_client):
        client = logged_in_client()
        response = client.put("/api/v1/cameras/cam-xxx", json={"name": "x"})
        assert response.status_code == 404


class TestDeleteCamera:
    """Test DELETE /api/v1/cameras/<id>."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        assert client.delete("/api/v1/cameras/cam-001").status_code == 403

    def test_deletes_camera(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        response = client.delete("/api/v1/cameras/cam-001")
        assert response.status_code == 200
        assert app.store.get_camera("cam-001") is None

    def test_delete_nonexistent(self, logged_in_client):
        client = logged_in_client()
        response = client.delete("/api/v1/cameras/cam-xxx")
        assert response.status_code == 404


class TestCameraStatus:
    """Test GET /api/v1/cameras/<id>/status."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/cameras/cam-001/status").status_code == 401

    def test_returns_status(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online", "Front Door")
        data = client.get("/api/v1/cameras/cam-001/status").get_json()
        assert data["id"] == "cam-001"
        assert data["status"] == "online"

    def test_status_not_found(self, logged_in_client):
        client = logged_in_client()
        response = client.get("/api/v1/cameras/cam-xxx/status")
        assert response.status_code == 404


class TestCamerasAuditLog:
    """Test audit logging for camera operations."""

    def test_confirm_logged(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "pending")
        client.post("/api/v1/cameras/cam-001/confirm", json={"name": "Front"})
        events = app.audit.get_events(event_type="CAMERA_CONFIRMED")
        assert len(events) >= 1

    def test_delete_logged(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, "cam-001", "online")
        client.delete("/api/v1/cameras/cam-001")
        events = app.audit.get_events(event_type="CAMERA_DELETED")
        assert len(events) >= 1


# ---------------------------------------------------------------------------
# HMAC camera M2M endpoints — config-notify, heartbeat, goodbye
# ---------------------------------------------------------------------------

import hashlib
import hmac as _hmac_lib
import time as _time

_PAIRING_SECRET = "deadbeef" * 8  # 64 hex chars = 32 bytes


def _make_camera_with_secret(app, camera_id="cam-001", status="online"):
    cam = Camera(id=camera_id, status=status, ip="192.168.1.50",
                 pairing_secret=_PAIRING_SECRET)
    app.store.save_camera(cam)
    return cam


def _hmac_headers(camera_id: str, body: bytes = b"{}") -> dict:
    """Build valid HMAC auth headers for a camera M2M request."""
    timestamp = str(int(_time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{camera_id}:{timestamp}:{body_hash}"
    sig = _hmac_lib.new(
        bytes.fromhex(_PAIRING_SECRET),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Camera-ID": camera_id,
        "X-Timestamp": timestamp,
        "X-Signature": sig,
    }


class TestCameraHMACAuth:
    """HMAC auth error paths — shared by heartbeat, config-notify, goodbye."""

    def test_missing_all_headers_returns_401(self, app, client):
        _make_camera_with_secret(app)
        resp = client.post("/api/v1/cameras/heartbeat", json={})
        assert resp.status_code == 401
        assert "Missing auth headers" in resp.get_json()["error"]

    def test_missing_camera_id_returns_401(self, app, client):
        _make_camera_with_secret(app)
        resp = client.post(
            "/api/v1/cameras/heartbeat",
            json={},
            headers={"X-Timestamp": "123", "X-Signature": "abc"},
        )
        assert resp.status_code == 401

    def test_invalid_timestamp_returns_400(self, app, client):
        _make_camera_with_secret(app)
        resp = client.post(
            "/api/v1/cameras/heartbeat",
            json={},
            headers={
                "X-Camera-ID": "cam-001",
                "X-Timestamp": "not-a-number",
                "X-Signature": "abc",
            },
        )
        assert resp.status_code == 400
        assert "Invalid timestamp" in resp.get_json()["error"]

    def test_expired_timestamp_returns_401(self, app, client):
        _make_camera_with_secret(app)
        old_ts = str(int(_time.time()) - 120)  # 2 minutes ago
        resp = client.post(
            "/api/v1/cameras/heartbeat",
            json={},
            headers={
                "X-Camera-ID": "cam-001",
                "X-Timestamp": old_ts,
                "X-Signature": "abc",
            },
        )
        assert resp.status_code == 401
        assert "Timestamp expired" in resp.get_json()["error"]

    def test_unknown_camera_returns_401(self, app, client):
        # Camera not in store
        resp = client.post(
            "/api/v1/cameras/heartbeat",
            json={},
            headers={
                "X-Camera-ID": "cam-ghost",
                "X-Timestamp": str(int(_time.time())),
                "X-Signature": "abc",
            },
        )
        assert resp.status_code == 401
        assert "Unknown camera" in resp.get_json()["error"]

    def test_bad_signature_returns_401(self, app, client):
        _make_camera_with_secret(app)
        headers = _hmac_headers("cam-001", b"{}")
        headers["X-Signature"] = "badsignaturevalue" + "0" * 47
        resp = client.post("/api/v1/cameras/heartbeat", json={},
                           headers=headers)
        assert resp.status_code == 401
        assert "Invalid signature" in resp.get_json()["error"]

    def test_replay_rejected(self, app, client):
        """Sending the identical signed request twice returns 401 on the second."""
        _make_camera_with_secret(app)
        body = b'{"streaming": false}'
        headers = _hmac_headers("cam-001", body)
        # First request succeeds
        resp1 = client.post("/api/v1/cameras/heartbeat",
                            data=body, content_type="application/json",
                            headers=headers)
        assert resp1.status_code == 200
        # Exact same headers + body → replay
        resp2 = client.post("/api/v1/cameras/heartbeat",
                            data=body, content_type="application/json",
                            headers=headers)
        assert resp2.status_code == 401
        assert "replay" in resp2.get_json()["error"].lower()


class TestConfigNotifyEndpoint:
    """POST /api/v1/cameras/config-notify."""

    def test_accepts_valid_config(self, app, client):
        _make_camera_with_secret(app)
        body = b'{"fps": 15}'
        headers = _hmac_headers("cam-001", body)
        resp = client.post("/api/v1/cameras/config-notify",
                           data=body, content_type="application/json",
                           headers=headers)
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Config accepted"

    def test_missing_json_body_returns_400(self, app, client):
        _make_camera_with_secret(app)
        body = b""
        headers = _hmac_headers("cam-001", body)
        resp = client.post("/api/v1/cameras/config-notify",
                           data=body, content_type="application/json",
                           headers=headers)
        assert resp.status_code == 400

    def test_unknown_param_returns_400(self, app, client):
        _make_camera_with_secret(app)
        body = b'{"bogus_param": 99}'
        headers = _hmac_headers("cam-001", body)
        resp = client.post("/api/v1/cameras/config-notify",
                           data=body, content_type="application/json",
                           headers=headers)
        assert resp.status_code == 400

    def test_rejects_unauthenticated(self, app, client):
        _make_camera_with_secret(app)
        resp = client.post("/api/v1/cameras/config-notify", json={"fps": 15})
        assert resp.status_code == 401


class TestCameraHeartbeatEndpoint:
    """POST /api/v1/cameras/heartbeat."""

    def test_updates_camera_status(self, app, client):
        cam = _make_camera_with_secret(app)
        cam.status = "offline"
        app.store.save_camera(cam)
        body = b'{"streaming": true, "cpu_temp": 52.5, "memory_percent": 40}'
        headers = _hmac_headers("cam-001", body)
        resp = client.post("/api/v1/cameras/heartbeat",
                           data=body, content_type="application/json",
                           headers=headers)
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        updated = app.store.get_camera("cam-001")
        assert updated.status == "online"
        assert updated.cpu_temp == 52.5

    def test_returns_pending_config_when_sync_pending(self, app, client):
        cam = _make_camera_with_secret(app)
        cam.config_sync = "pending"
        cam.fps = 20
        app.store.save_camera(cam)
        body = b'{"streaming": false}'
        headers = _hmac_headers("cam-001", body)
        resp = client.post("/api/v1/cameras/heartbeat",
                           data=body, content_type="application/json",
                           headers=headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert "pending_config" in data
        assert data["pending_config"]["fps"] == 20

    def test_missing_json_body_returns_400(self, app, client):
        _make_camera_with_secret(app)
        body = b""
        headers = _hmac_headers("cam-001", body)
        resp = client.post("/api/v1/cameras/heartbeat",
                           data=body, content_type="application/json",
                           headers=headers)
        assert resp.status_code == 400

    def test_unknown_camera_triggers_discovery(self, app, client):
        """Heartbeat from unknown camera creates a pending entry via discovery."""
        from unittest.mock import MagicMock
        app.discovery_service = MagicMock()
        resp = client.post(
            "/api/v1/cameras/heartbeat",
            json={},
            headers={
                "X-Camera-ID": "cam-ghost",
                "X-Timestamp": str(int(_time.time())),
                "X-Signature": "abc",
            },
        )
        assert resp.status_code == 401
        app.discovery_service.report_camera.assert_called_once()


class TestCameraGoodbyeEndpoint:
    """POST /api/v1/cameras/goodbye."""

    def test_unpairs_camera(self, app, client):
        _make_camera_with_secret(app)
        body = b"{}"
        headers = _hmac_headers("cam-001", body)
        resp = client.post("/api/v1/cameras/goodbye",
                           data=body, content_type="application/json",
                           headers=headers)
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Camera unpaired"

    def test_stops_streaming_on_goodbye(self, app, client):
        from unittest.mock import MagicMock
        _make_camera_with_secret(app)
        app.streaming.stop_camera = MagicMock()
        body = b"{}"
        headers = _hmac_headers("cam-001", body)
        client.post("/api/v1/cameras/goodbye",
                    data=body, content_type="application/json",
                    headers=headers)
        app.streaming.stop_camera.assert_called_once_with("cam-001")

    def test_rejects_unauthenticated(self, app, client):
        _make_camera_with_secret(app)
        resp = client.post("/api/v1/cameras/goodbye", json={})
        assert resp.status_code == 401
