"""Tests for the pairing API endpoints."""

import os
from unittest.mock import patch

import pytest

from monitor.models import Camera, User


@pytest.fixture(autouse=True)
def _clear_register_rate_limiter():
    """Reset the module-level register rate-limit dict between tests."""
    import monitor.api.pairing as _pairing_mod
    _pairing_mod._register_attempts.clear()
    yield
    _pairing_mod._register_attempts.clear()


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

    def test_viewer_cannot_pair(self, logged_in_client):
        client = logged_in_client("viewer")
        resp = client.post("/api/v1/cameras/cam-001/pair")
        assert resp.status_code == 403

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_returns_pin_on_success(self, mock_gen, app, logged_in_client):
        client = logged_in_client()
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

    def test_returns_404_for_unknown_camera(self, logged_in_client):
        client = logged_in_client()
        resp = client.post("/api/v1/cameras/nonexistent/pair")
        assert resp.status_code == 404

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_rejects_online_camera(self, mock_gen, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, status="online")
        resp = client.post("/api/v1/cameras/cam-001/pair")
        assert resp.status_code == 400


class TestUnpairCamera:
    """Test POST /api/v1/cameras/<id>/unpair."""

    def test_requires_admin(self, client):
        resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 401

    def test_viewer_cannot_unpair(self, logged_in_client):
        client = logged_in_client("viewer")
        resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 403

    def test_unpairs_camera(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, status="online")
        resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 200
        assert resp.get_json()["message"] == "Camera unpaired"

    def test_returns_404_for_unknown_camera(self, logged_in_client):
        client = logged_in_client()
        resp = client.post("/api/v1/cameras/nonexistent/unpair")
        assert resp.status_code == 404

    def test_unpair_stops_streaming(self, app, logged_in_client):
        """Unpair should stop the streaming pipeline."""
        from unittest.mock import MagicMock

        client = logged_in_client()
        _add_camera(app, status="online")
        app.streaming.stop_camera = MagicMock()

        resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 200
        app.streaming.stop_camera.assert_called_once_with("cam-001")

    def test_unpair_unknown_camera(self, logged_in_client):
        """Unpair non-existent camera returns 404."""
        client = logged_in_client()
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
        # Should not return 401 â€” instead returns 404 (no pending pairing)
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
    def test_full_pairing_flow(self, mock_gen, app, logged_in_client):
        """End-to-end: initiate as admin, exchange as camera."""
        client = logged_in_client()
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
        self, mock_gen, app, logged_in_client
    ):
        """Streaming pipeline starts automatically after successful pairing."""
        from unittest.mock import MagicMock

        client = logged_in_client()
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
    def test_exchange_skips_streaming_for_on_demand_camera(self, mock_gen, app, logged_in_client):
        """Streaming pipeline does NOT start if recording_mode is on_demand."""
        from unittest.mock import MagicMock

        client = logged_in_client()
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
    def test_wrong_pin_rejected(self, mock_gen, app, logged_in_client):
        """Wrong PIN returns 403."""
        client = logged_in_client()
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

    def test_delete_calls_unpair(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app, status="online")
        resp = client.delete("/api/v1/cameras/cam-001")
        assert resp.status_code == 200


class TestRegisterCamera:
    """POST /api/v1/pair/register — camera self-registers as pending."""

    def test_returns_registered_on_success(self, app, client):
        resp = client.post(
            "/api/v1/pair/register",
            json={"camera_id": "cam-abc123", "firmware_version": "1.0.0"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "registered"

    def test_requires_json_body(self, client):
        resp = client.post("/api/v1/pair/register")
        assert resp.status_code == 400
        assert "JSON body" in resp.get_json()["error"]

    def test_requires_camera_id(self, client):
        resp = client.post("/api/v1/pair/register", json={"firmware_version": "1.0"})
        assert resp.status_code == 400
        assert "camera_id" in resp.get_json()["error"]

    def test_rejects_invalid_camera_id_format(self, client):
        for bad_id in ["CAM-001", "cam_001", "cam-", "x" * 60, "cam-UPPER"]:
            resp = client.post("/api/v1/pair/register", json={"camera_id": bad_id})
            assert resp.status_code == 400, f"Expected 400 for {bad_id}"

    def test_accepts_valid_camera_id_formats(self, client):
        for good_id in ["cam-001", "cam-abc123", "cam-a1b2c3"]:
            resp = client.post("/api/v1/pair/register", json={"camera_id": good_id})
            assert resp.status_code == 200, f"Expected 200 for {good_id}"

    def test_rate_limited_after_10_requests(self, app, client):
        """11th registration from same IP is rejected with 429."""
        for _ in range(10):
            client.post("/api/v1/pair/register", json={"camera_id": f"cam-{_:03d}"})
        resp = client.post("/api/v1/pair/register", json={"camera_id": "cam-999"})
        assert resp.status_code == 429
        assert "Too many" in resp.get_json()["error"]

    def test_does_not_require_session_auth(self, client):
        """register is open — no login needed."""
        resp = client.post("/api/v1/pair/register", json={"camera_id": "cam-open1"})
        assert resp.status_code != 401

    def test_creates_pending_camera_in_store(self, app, client):
        client.post(
            "/api/v1/pair/register",
            json={"camera_id": "cam-reg01", "firmware_version": "2.0.0"},
        )
        cam = app.store.get_camera("cam-reg01")
        assert cam is not None
        assert cam.status == "pending"


class TestSafePairingError:
    """_safe_pairing_error() sanitises internal messages."""

    def test_sanitises_ca_key_error(self, app, logged_in_client):
        """CA cert path must not leak to the client."""
        from unittest.mock import patch
        with patch(
            "monitor.services.pairing_service.PairingService.initiate_pairing",
            return_value=(None, "CA key or certificate not found at /data/certs/ca.key", 500),
        ):
            from monitor.models import Camera
            app.store.save_camera(Camera(id="cam-001", status="pending", ip="192.168.1.1"))
            client = logged_in_client()
            resp = client.post("/api/v1/cameras/cam-001/pair")
        assert resp.status_code == 500
        body = resp.get_json()["error"]
        assert "/data/certs" not in body
        assert "administrator" in body.lower()

    def test_sanitises_openssl_error(self, app, logged_in_client):
        from unittest.mock import patch
        with patch(
            "monitor.services.pairing_service.PairingService.initiate_pairing",
            return_value=(None, "OpenSSL error: bad signature", 500),
        ):
            from monitor.models import Camera
            app.store.save_camera(Camera(id="cam-002", status="pending", ip="192.168.1.2"))
            client = logged_in_client()
            resp = client.post("/api/v1/cameras/cam-002/pair")
        assert resp.status_code == 500
        assert "OpenSSL" not in resp.get_json()["error"]

    def test_plain_error_passes_through(self, app, logged_in_client):
        from unittest.mock import patch
        with patch(
            "monitor.services.pairing_service.PairingService.initiate_pairing",
            return_value=(None, "Camera already online", 400),
        ):
            from monitor.models import Camera
            app.store.save_camera(Camera(id="cam-003", status="online", ip="192.168.1.3"))
            client = logged_in_client()
            resp = client.post("/api/v1/cameras/cam-003/pair")
        assert resp.status_code == 400
        assert "already online" in resp.get_json()["error"].lower()


class TestUnpairCameraErrorPath:
    """POST /api/v1/cameras/<id>/unpair error path."""

    def test_unpair_service_error_returned(self, app, logged_in_client):
        from unittest.mock import patch
        with patch(
            "monitor.services.pairing_service.PairingService.unpair",
            return_value=("Internal revocation error", 500),
        ):
            from monitor.models import Camera
            app.store.save_camera(Camera(id="cam-001", status="online", ip="192.168.1.50"))
            client = logged_in_client()
            resp = client.post("/api/v1/cameras/cam-001/unpair")
        assert resp.status_code == 500
        assert "error" in resp.get_json()
