"""Tests for the pairing API endpoints."""

import os
from unittest.mock import patch

import pytest

from monitor.auth import hash_password
from monitor.models import Camera, User


@pytest.fixture(autouse=True)
def _setup_ca_files(app):
    """Create fake CA files so PairingService can read them."""
    certs_dir = app.config["CERTS_DIR"]
    os.makedirs(os.path.join(certs_dir, "cameras", "revoked"), exist_ok=True)
    ca_crt = os.path.join(certs_dir, "ca.crt")
    ca_key = os.path.join(certs_dir, "ca.key")
    if not os.path.exists(ca_crt):
        with open(ca_crt, "w") as f:
            f.write("FAKE CA CERT FOR TESTING")
    if not os.path.exists(ca_key):
        with open(ca_key, "w") as f:
            f.write("FAKE CA KEY FOR TESTING")


def _login(app, client, role="admin"):
    """Helper: create admin user and login."""
    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash=hash_password("pass"),
            role=role,
        )
    )
    response = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "pass"}
    )
    client.environ_base["HTTP_X_CSRF_TOKEN"] = response.get_json()["csrf_token"]


def _add_camera(app, camera_id="cam-001", status="pending"):
    """Helper: add a camera to the store."""
    camera = Camera(id=camera_id, status=status, ip="192.168.1.50")
    app.store.save_camera(camera)
    return camera


class TestInitiatePairing:
    """Test POST /api/v1/cameras/<id>/pair."""

    def test_requires_admin(self, client):
        resp = client.post("/api/v1/cameras/cam-001/pair")
        assert resp.status_code == 401

    def test_viewer_cannot_pair(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/cameras/cam-001/pair")
        assert resp.status_code == 403

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_returns_pin_on_success(self, mock_gen, app, client):
        _login(app, client)
        _add_camera(app)
        mock_gen.return_value = (
            {"cert": "CERT", "key": "KEY", "serial": "ABC123"},
            "",
        )
        resp = client.post("/api/v1/cameras/cam-001/pair")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "pin" in data
        assert len(data["pin"]) == 6
        assert data["expires_in"] == 300

    def test_returns_404_for_unknown_camera(self, app, client):
        _login(app, client)
        resp = client.post("/api/v1/cameras/nonexistent/pair")
        assert resp.status_code == 404

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_rejects_online_camera(self, mock_gen, app, client):
        _login(app, client)
        _add_camera(app, status="online")
        resp = client.post("/api/v1/cameras/cam-001/pair")
        assert resp.status_code == 400


class TestUnpairCamera:
    """Test POST /api/v1/cameras/<id>/unpair."""

    def test_requires_admin(self, client):
        resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 401

    def test_viewer_cannot_unpair(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 403

    def test_unpairs_camera(self, app, client):
        _login(app, client)
        _add_camera(app, status="online")
        resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Camera unpaired"

    def test_returns_404_for_unknown_camera(self, app, client):
        _login(app, client)
        resp = client.post("/api/v1/cameras/nonexistent/unpair")
        assert resp.status_code == 404

    def test_unpair_stops_streaming(self, app, client):
        """Unpair should stop the streaming pipeline."""
        from unittest.mock import MagicMock

        _login(app, client)
        _add_camera(app, status="online")
        app.streaming.stop_camera = MagicMock()

        resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 200
        app.streaming.stop_camera.assert_called_once_with("cam-001")

    def test_unpair_unknown_camera(self, app, client):
        """Unpair non-existent camera returns 404."""
        _login(app, client)
        resp = client.post("/api/v1/cameras/no-such-cam/unpair")
        assert resp.status_code == 404


class TestExchangeCerts:
    """Test POST /api/v1/pair/exchange."""

    def test_does_not_require_session_auth(self, app, client):
        """Exchange endpoint uses PIN auth, not session auth."""
        resp = client.post(
            "/api/v1/pair/exchange",
            json={"pin": "123456", "camera_id": "cam-001"},
        )
        # Should not return 401 — instead returns 404 (no pending pairing)
        assert resp.status_code != 401

    def test_requires_json_body(self, client):
        resp = client.post("/api/v1/pair/exchange")
        assert resp.status_code == 400
        assert "JSON body" in resp.get_json()["error"]

    def test_requires_pin_and_camera_id(self, client):
        resp = client.post("/api/v1/pair/exchange", json={"pin": "123456"})
        assert resp.status_code == 400
        assert "required" in resp.get_json()["error"]

    def test_requires_camera_id(self, client):
        resp = client.post("/api/v1/pair/exchange", json={"camera_id": "cam-001"})
        assert resp.status_code == 400

    def test_returns_404_when_no_pending(self, app, client):
        resp = client.post(
            "/api/v1/pair/exchange",
            json={"pin": "123456", "camera_id": "cam-001"},
        )
        assert resp.status_code == 404

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_full_pairing_flow(self, mock_gen, app, client):
        """End-to-end: initiate as admin, exchange as camera."""
        _login(app, client)
        _add_camera(app)
        mock_gen.return_value = (
            {"cert": "CLIENT CERT", "key": "CLIENT KEY", "serial": "ABC123"},
            "",
        )

        # Admin initiates
        resp = client.post("/api/v1/cameras/cam-001/pair")
        assert resp.status_code == 200
        pin = resp.get_json()["pin"]

        # Camera exchanges (new client without session)
        with app.test_client() as camera_client:
            resp = camera_client.post(
                "/api/v1/pair/exchange",
                json={"pin": pin, "camera_id": "cam-001"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["client_cert"] == "CLIENT CERT"
            assert data["client_key"] == "CLIENT KEY"
            assert "ca_cert" in data
            assert "pairing_secret" in data
            assert "rtsps_url" in data

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_exchange_starts_streaming_for_continuous_camera(
        self, mock_gen, app, client
    ):
        """Streaming pipeline starts automatically after successful pairing."""
        from unittest.mock import MagicMock

        _login(app, client)
        cam = _add_camera(app)
        cam.recording_mode = "continuous"
        app.store.save_camera(cam)
        mock_gen.return_value = (
            {"cert": "CERT", "key": "KEY", "serial": "S1"},
            "",
        )
        app.streaming.start_camera = MagicMock()

        resp = client.post("/api/v1/cameras/cam-001/pair")
        pin = resp.get_json()["pin"]

        with app.test_client() as camera_client:
            camera_client.post(
                "/api/v1/pair/exchange",
                json={"pin": pin, "camera_id": "cam-001"},
            )

        app.streaming.start_camera.assert_called_once_with("cam-001")

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_exchange_skips_streaming_for_on_demand_camera(self, mock_gen, app, client):
        """Streaming pipeline does NOT start if recording_mode is on_demand."""
        from unittest.mock import MagicMock

        _login(app, client)
        cam = _add_camera(app)
        cam.recording_mode = "on_demand"
        app.store.save_camera(cam)
        mock_gen.return_value = (
            {"cert": "CERT", "key": "KEY", "serial": "S2"},
            "",
        )
        app.streaming.start_camera = MagicMock()

        resp = client.post("/api/v1/cameras/cam-001/pair")
        pin = resp.get_json()["pin"]

        with app.test_client() as camera_client:
            camera_client.post(
                "/api/v1/pair/exchange",
                json={"pin": pin, "camera_id": "cam-001"},
            )

        app.streaming.start_camera.assert_not_called()

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_wrong_pin_rejected(self, mock_gen, app, client):
        """Wrong PIN returns 403."""
        _login(app, client)
        _add_camera(app)
        mock_gen.return_value = (
            {"cert": "CERT", "key": "KEY", "serial": "S"},
            "",
        )
        client.post("/api/v1/cameras/cam-001/pair")

        with app.test_client() as camera_client:
            resp = camera_client.post(
                "/api/v1/pair/exchange",
                json={"pin": "000000", "camera_id": "cam-001"},
            )
            assert resp.status_code == 403


class TestDeleteCameraUnpairs:
    """Test that DELETE /cameras/<id> also revokes certs."""

    def test_delete_calls_unpair(self, app, client):
        _login(app, client)
        _add_camera(app, status="online")
        resp = client.delete("/api/v1/cameras/cam-001")
        assert resp.status_code == 200
