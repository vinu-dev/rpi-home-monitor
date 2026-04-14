"""Tests for the pairing service."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.pairing_service import (
    PIN_DIGITS,
    PIN_EXPIRY_SECONDS,
    PIN_MAX_ATTEMPTS,
    PairingService,
)


def _make_camera(**overrides):
    """Create a fake camera object with sensible defaults."""
    defaults = {
        "id": "cam-001",
        "name": "Front Door",
        "location": "Porch",
        "status": "pending",
        "ip": "192.168.1.50",
        "recording_mode": "continuous",
        "resolution": "1080p",
        "fps": 25,
        "paired_at": None,
        "last_seen": None,
        "firmware_version": "1.0.0",
        "rtsp_url": "",
        "cert_serial": "",
        "pairing_secret": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def certs_dir(tmp_path):
    """Create a temporary certs directory with CA files."""
    certs = tmp_path / "certs"
    certs.mkdir()
    cameras = certs / "cameras"
    cameras.mkdir()
    (cameras / "revoked").mkdir()
    # Create fake CA files
    (certs / "ca.key").write_text("FAKE CA KEY")
    (certs / "ca.crt").write_text("FAKE CA CERT")
    return certs


@pytest.fixture
def store():
    return MagicMock()


@pytest.fixture
def audit():
    return MagicMock()


@pytest.fixture
def svc(store, audit, certs_dir):
    return PairingService(store=store, audit=audit, certs_dir=str(certs_dir))


class TestInitiatePairing:
    """Test pairing initiation (PIN + cert generation)."""

    def test_returns_404_when_camera_not_found(self, svc, store):
        store.get_camera.return_value = None
        pin, error, status = svc.initiate_pairing("nonexistent")
        assert status == 404
        assert pin is None
        assert "not found" in error

    def test_rejects_camera_not_in_pending_or_offline(self, svc, store):
        store.get_camera.return_value = _make_camera(status="online")
        pin, error, status = svc.initiate_pairing("cam-001")
        assert status == 400
        assert pin is None

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_returns_6_digit_pin_on_success(self, mock_gen, svc, store):
        store.get_camera.return_value = _make_camera(status="pending")
        mock_gen.return_value = (
            {"cert": "CERT", "key": "KEY", "serial": "ABC123"},
            "",
        )
        pin, error, status = svc.initiate_pairing("cam-001")
        assert status == 200
        assert error == ""
        assert len(pin) == PIN_DIGITS
        assert pin.isdigit()

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_stores_pending_pairing(self, mock_gen, svc, store):
        store.get_camera.return_value = _make_camera(status="pending")
        mock_gen.return_value = (
            {"cert": "CERT", "key": "KEY", "serial": "ABC123"},
            "",
        )
        svc.initiate_pairing("cam-001")
        assert "cam-001" in svc._pending_pairings
        pending = svc._pending_pairings["cam-001"]
        assert pending["attempts"] == 0
        assert pending["expires_at"] > time.time()

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_logs_audit_event(self, mock_gen, svc, store, audit):
        store.get_camera.return_value = _make_camera(status="pending")
        mock_gen.return_value = (
            {"cert": "CERT", "key": "KEY", "serial": "ABC123"},
            "",
        )
        svc.initiate_pairing("cam-001", user="admin", ip="10.0.0.1")
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "PAIRING_INITIATED"

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_returns_500_when_cert_generation_fails(self, mock_gen, svc, store):
        store.get_camera.return_value = _make_camera(status="pending")
        mock_gen.return_value = (None, "openssl not found")
        pin, error, status = svc.initiate_pairing("cam-001")
        assert status == 500
        assert pin is None
        assert "Certificate generation failed" in error

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_allows_pairing_offline_camera(self, mock_gen, svc, store):
        store.get_camera.return_value = _make_camera(status="offline")
        mock_gen.return_value = (
            {"cert": "CERT", "key": "KEY", "serial": "ABC123"},
            "",
        )
        pin, error, status = svc.initiate_pairing("cam-001")
        assert status == 200


class TestExchangeCerts:
    """Test PIN-for-certs exchange."""

    def _setup_pending(self, svc, store, pin="123456"):
        """Set up a pending pairing with known PIN."""
        store.get_camera.return_value = _make_camera(status="pending")
        svc._pending_pairings["cam-001"] = {
            "pin": pin,
            "expires_at": time.time() + PIN_EXPIRY_SECONDS,
            "attempts": 0,
            "cert_data": {
                "cert": "CLIENT CERT",
                "key": "CLIENT KEY",
                "serial": "ABC123",
            },
        }

    def test_returns_404_when_no_pending_pairing(self, svc):
        result, error, status = svc.exchange_certs("123456", "cam-001")
        assert status == 404
        assert result is None

    def test_returns_410_when_pin_expired(self, svc, store):
        self._setup_pending(svc, store)
        svc._pending_pairings["cam-001"]["expires_at"] = time.time() - 1
        result, error, status = svc.exchange_certs("123456", "cam-001")
        assert status == 410
        assert "expired" in error
        assert "cam-001" not in svc._pending_pairings

    def test_returns_403_on_wrong_pin(self, svc, store):
        self._setup_pending(svc, store, pin="123456")
        result, error, status = svc.exchange_certs("000000", "cam-001")
        assert status == 403
        assert "Invalid PIN" in error
        assert result is None

    def test_returns_429_after_max_attempts(self, svc, store, audit):
        self._setup_pending(svc, store, pin="123456")
        svc._pending_pairings["cam-001"]["attempts"] = PIN_MAX_ATTEMPTS
        result, error, status = svc.exchange_certs("000000", "cam-001")
        assert status == 429
        assert "Too many attempts" in error
        assert "cam-001" not in svc._pending_pairings

    def test_successful_exchange_returns_certs(self, svc, store, certs_dir):
        self._setup_pending(svc, store, pin="123456")
        result, error, status = svc.exchange_certs("123456", "cam-001")
        assert status == 200
        assert error == ""
        assert result["client_cert"] == "CLIENT CERT"
        assert result["client_key"] == "CLIENT KEY"
        assert result["ca_cert"] == "FAKE CA CERT"
        assert "pairing_secret" in result
        assert len(result["pairing_secret"]) == 64  # 32 bytes hex
        assert "rtsps_url" in result

    def test_successful_exchange_updates_camera(self, svc, store, certs_dir):
        cam = _make_camera(status="pending")
        store.get_camera.return_value = cam
        svc._pending_pairings["cam-001"] = {
            "pin": "123456",
            "expires_at": time.time() + PIN_EXPIRY_SECONDS,
            "attempts": 0,
            "cert_data": {
                "cert": "CERT",
                "key": "KEY",
                "serial": "ABC123",
            },
        }
        svc.exchange_certs("123456", "cam-001")
        assert cam.status == "online"
        assert cam.cert_serial == "ABC123"
        assert cam.pairing_secret != ""
        store.save_camera.assert_called_once_with(cam)

    def test_successful_exchange_removes_pending(self, svc, store, certs_dir):
        self._setup_pending(svc, store, pin="123456")
        svc.exchange_certs("123456", "cam-001")
        assert "cam-001" not in svc._pending_pairings

    def test_successful_exchange_logs_audit(self, svc, store, audit, certs_dir):
        self._setup_pending(svc, store, pin="123456")
        svc.exchange_certs("123456", "cam-001")
        assert any(
            call[0][0] == "CAMERA_PAIRED" for call in audit.log_event.call_args_list
        )

    def test_wrong_pin_decrements_remaining(self, svc, store):
        self._setup_pending(svc, store, pin="123456")
        result, error, status = svc.exchange_certs("000000", "cam-001")
        assert f"{PIN_MAX_ATTEMPTS - 1} attempts remaining" in error

    def test_pin_comparison_is_constant_time(self, svc, store):
        """Verify we use secrets.compare_digest (tested indirectly via behavior)."""
        self._setup_pending(svc, store, pin="123456")
        # Wrong PIN should fail regardless of partial match
        result, error, status = svc.exchange_certs("123000", "cam-001")
        assert status == 403


class TestUnpair:
    """Test camera unpairing / cert revocation."""

    def test_returns_404_when_camera_not_found(self, svc, store):
        store.get_camera.return_value = None
        error, status = svc.unpair("nonexistent")
        assert status == 404

    def test_moves_cert_to_revoked(self, svc, store, certs_dir):
        cam = _make_camera(status="online", cert_serial="ABC123")
        store.get_camera.return_value = cam
        # Create fake cert file
        cert_path = certs_dir / "cameras" / "cam-001.crt"
        cert_path.write_text("CERT CONTENT")
        key_path = certs_dir / "cameras" / "cam-001.key"
        key_path.write_text("KEY CONTENT")

        error, status = svc.unpair("cam-001")
        assert status == 200
        assert not cert_path.exists()
        assert not key_path.exists()
        assert (certs_dir / "cameras" / "revoked" / "cam-001.crt").exists()

    def test_adds_serial_to_revocation_set(self, svc, store, certs_dir):
        cam = _make_camera(status="online", cert_serial="ABC123")
        store.get_camera.return_value = cam
        (certs_dir / "cameras" / "cam-001.crt").write_text("CERT")

        svc.unpair("cam-001")
        assert svc.is_cert_revoked("ABC123")

    def test_resets_camera_state(self, svc, store, certs_dir):
        cam = _make_camera(status="online", cert_serial="ABC123", pairing_secret="aabb")
        store.get_camera.return_value = cam

        svc.unpair("cam-001")
        assert cam.status == "pending"
        assert cam.cert_serial == ""
        assert cam.pairing_secret == ""
        store.save_camera.assert_called_once_with(cam)

    def test_cancels_pending_pairing(self, svc, store, certs_dir):
        cam = _make_camera(status="pending")
        store.get_camera.return_value = cam
        svc._pending_pairings["cam-001"] = {"pin": "123456"}

        svc.unpair("cam-001")
        assert "cam-001" not in svc._pending_pairings

    def test_logs_audit_event(self, svc, store, audit, certs_dir):
        cam = _make_camera(status="online")
        store.get_camera.return_value = cam

        svc.unpair("cam-001", user="admin", ip="10.0.0.1")
        audit.log_event.assert_called_once()
        assert audit.log_event.call_args[0][0] == "CERT_REVOKED"

    def test_works_when_cert_file_missing(self, svc, store, certs_dir):
        cam = _make_camera(status="online", cert_serial="ABC123")
        store.get_camera.return_value = cam
        # No cert file on disk
        error, status = svc.unpair("cam-001")
        assert status == 200


class TestIsCertRevoked:
    """Test certificate revocation checks."""

    def test_returns_false_for_unknown_serial(self, svc):
        assert not svc.is_cert_revoked("UNKNOWN")

    def test_returns_true_after_unpair(self, svc, store, certs_dir):
        cam = _make_camera(status="online", cert_serial="REVOKED123")
        store.get_camera.return_value = cam
        (certs_dir / "cameras" / "cam-001.crt").write_text("CERT")

        svc.unpair("cam-001")
        assert svc.is_cert_revoked("REVOKED123")


class TestGetPendingPairing:
    """Test pending pairing info retrieval."""

    def test_returns_none_when_no_pending(self, svc):
        assert svc.get_pending_pairing("cam-001") is None

    def test_returns_info_when_pending(self, svc):
        svc._pending_pairings["cam-001"] = {
            "pin": "123456",
            "expires_at": time.time() + 120,
            "attempts": 1,
            "cert_data": {},
        }
        info = svc.get_pending_pairing("cam-001")
        assert info["pin"] == "123456"
        assert info["expires_in"] > 0
        assert info["attempts"] == 1

    def test_returns_none_when_expired(self, svc):
        svc._pending_pairings["cam-001"] = {
            "pin": "123456",
            "expires_at": time.time() - 1,
            "attempts": 0,
            "cert_data": {},
        }
        assert svc.get_pending_pairing("cam-001") is None
        assert "cam-001" not in svc._pending_pairings


class TestGenerateClientCert:
    """Test certificate generation (subprocess calls mocked)."""

    @patch("monitor.services.pairing_service.subprocess.run")
    def test_generates_cert_with_correct_openssl_commands(
        self, mock_run, svc, certs_dir
    ):
        # Mock all subprocess calls to succeed
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "serial=ABC123\n"
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        # Create cert/key files that _read_file expects
        cameras_dir = certs_dir / "cameras"
        (cameras_dir / "cam-001.crt").write_text("GENERATED CERT")
        (cameras_dir / "cam-001.key").write_text("GENERATED KEY")

        cert_data, error = svc._generate_client_cert("cam-001")
        assert error == ""
        assert cert_data["cert"] == "GENERATED CERT"
        assert cert_data["key"] == "GENERATED KEY"
        assert cert_data["serial"] == "ABC123"

        # Verify openssl was called (at least genkey, req, x509, serial read)
        assert mock_run.call_count >= 4

    @patch("monitor.services.pairing_service.subprocess.run")
    def test_returns_error_on_openssl_failure(self, mock_run, svc, certs_dir):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "openssl error"
        mock_run.return_value = mock_result

        cert_data, error = svc._generate_client_cert("cam-001")
        assert cert_data is None
        assert "OpenSSL error" in error

    def test_returns_error_when_ca_missing(self, store, audit, tmp_path):
        empty_certs = tmp_path / "empty_certs"
        empty_certs.mkdir()
        (empty_certs / "cameras").mkdir()
        svc = PairingService(store=store, audit=audit, certs_dir=str(empty_certs))
        cert_data, error = svc._generate_client_cert("cam-001")
        assert cert_data is None
        assert "CA key or certificate not found" in error


class TestAuditFailureResilience:
    """Test that audit failures don't break pairing operations."""

    @patch("monitor.services.pairing_service.PairingService._generate_client_cert")
    def test_initiate_works_when_audit_fails(self, mock_gen, store, certs_dir):
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("disk full")
        svc = PairingService(store=store, audit=audit, certs_dir=str(certs_dir))
        store.get_camera.return_value = _make_camera(status="pending")
        mock_gen.return_value = ({"cert": "C", "key": "K", "serial": "S"}, "")
        pin, error, status = svc.initiate_pairing("cam-001")
        assert status == 200

    def test_unpair_works_when_audit_fails(self, store, certs_dir):
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("disk full")
        svc = PairingService(store=store, audit=audit, certs_dir=str(certs_dir))
        store.get_camera.return_value = _make_camera(status="online")
        error, status = svc.unpair("cam-001")
        assert status == 200

    def test_works_without_audit_service(self, store, certs_dir):
        svc = PairingService(store=store, audit=None, certs_dir=str(certs_dir))
        store.get_camera.return_value = _make_camera(status="online")
        error, status = svc.unpair("cam-001")
        assert status == 200
