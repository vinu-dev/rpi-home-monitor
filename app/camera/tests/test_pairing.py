"""Tests for camera PairingManager and PAIRING lifecycle state."""

import json
import os
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from camera_streamer.pairing import PairingManager


@pytest.fixture
def pairing_config(data_dir):
    """Config mock with camera_id set."""
    config = MagicMock()
    config.camera_id = "cam-test001"
    config.certs_dir = str(data_dir / "certs")
    return config


@pytest.fixture
def pairing_mgr(pairing_config, data_dir):
    """PairingManager with temp certs dir."""
    return PairingManager(pairing_config, certs_dir=str(data_dir / "certs"))


class TestIsPaired:
    """Test the is_paired property."""

    def test_not_paired_when_no_cert(self, pairing_mgr):
        assert pairing_mgr.is_paired is False

    def test_paired_when_cert_exists(self, pairing_mgr, data_dir):
        cert_path = data_dir / "certs" / "client.crt"
        cert_path.write_text("CERT DATA")
        assert pairing_mgr.is_paired is True


class TestCertPaths:
    """Test certificate path properties."""

    def test_client_cert_path(self, pairing_mgr, data_dir):
        expected = os.path.join(str(data_dir / "certs"), "client.crt")
        assert pairing_mgr.client_cert_path == expected

    def test_client_key_path(self, pairing_mgr, data_dir):
        expected = os.path.join(str(data_dir / "certs"), "client.key")
        assert pairing_mgr.client_key_path == expected

    def test_ca_cert_path(self, pairing_mgr, data_dir):
        expected = os.path.join(str(data_dir / "certs"), "ca.crt")
        assert pairing_mgr.ca_cert_path == expected


class TestExchange:
    """Test the exchange method (PIN → certs)."""

    def test_fails_without_camera_id(self, data_dir):
        config = MagicMock()
        config.camera_id = ""
        mgr = PairingManager(config, certs_dir=str(data_dir / "certs"))
        ok, err = mgr.exchange("123456", "https://192.168.1.100")
        assert ok is False
        assert "Camera ID" in err

    @patch("camera_streamer.pairing.urllib.request.urlopen")
    def test_successful_exchange(self, mock_urlopen, pairing_mgr, data_dir):
        """Exchange stores certs on success."""
        response_data = {
            "client_cert": "CLIENT CERT DATA",
            "client_key": "CLIENT KEY DATA",
            "ca_cert": "CA CERT DATA",
            "pairing_secret": "abc123hex",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, err = pairing_mgr.exchange("123456", "https://192.168.1.100")
        assert ok is True
        assert err == ""

        # Verify certs were written
        certs_dir = data_dir / "certs"
        assert (certs_dir / "client.crt").read_text() == "CLIENT CERT DATA"
        assert (certs_dir / "client.key").read_text() == "CLIENT KEY DATA"
        assert (certs_dir / "ca.crt").read_text() == "CA CERT DATA"
        assert (certs_dir / "pairing_secret").read_text() == "abc123hex"

    @patch("camera_streamer.pairing.urllib.request.urlopen")
    def test_exchange_sends_correct_payload(self, mock_urlopen, pairing_mgr):
        """Exchange sends PIN and camera_id to server."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {
                "client_cert": "C",
                "client_key": "K",
                "ca_cert": "CA",
                "pairing_secret": "s",
            }
        ).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        pairing_mgr.exchange("654321", "https://10.0.0.1")

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == "https://10.0.0.1/api/v1/pair/exchange"
        body = json.loads(req.data)
        assert body["pin"] == "654321"
        assert body["camera_id"] == "cam-test001"

    @patch("camera_streamer.pairing.urllib.request.urlopen")
    def test_exchange_http_error(self, mock_urlopen, pairing_mgr):
        """HTTP error returns failure tuple."""
        import urllib.error

        error_resp = MagicMock()
        error_resp.read.return_value = json.dumps({"error": "Invalid PIN"}).encode()
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 403, "Forbidden", {}, error_resp
        )

        ok, err = pairing_mgr.exchange("000000", "https://192.168.1.100")
        assert ok is False
        assert "Invalid PIN" in err

    @patch("camera_streamer.pairing.urllib.request.urlopen")
    def test_exchange_network_error(self, mock_urlopen, pairing_mgr):
        """Network error returns failure tuple."""
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        ok, err = pairing_mgr.exchange("123456", "https://192.168.1.100")
        assert ok is False
        assert "Cannot reach server" in err

    @patch("camera_streamer.pairing.urllib.request.urlopen")
    def test_exchange_missing_cert_data(self, mock_urlopen, pairing_mgr):
        """Missing cert fields in response returns failure."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"client_cert": "C"}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, err = pairing_mgr.exchange("123456", "https://192.168.1.100")
        assert ok is False
        assert "Failed to store certificates" in err

    @patch("camera_streamer.pairing.urllib.request.urlopen")
    def test_exchange_no_pairing_secret(self, mock_urlopen, pairing_mgr, data_dir):
        """Exchange succeeds even without pairing_secret in response."""
        response_data = {
            "client_cert": "C",
            "client_key": "K",
            "ca_cert": "CA",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, err = pairing_mgr.exchange("123456", "https://192.168.1.100")
        assert ok is True


class TestStoreCerts:
    """Test _store_certs internal method."""

    def test_creates_certs_dir(self, pairing_config, tmp_path):
        """Creates certs directory if it doesn't exist."""
        new_certs = tmp_path / "new_certs"
        mgr = PairingManager(pairing_config, certs_dir=str(new_certs))
        mgr._store_certs({"client_cert": "C", "client_key": "K", "ca_cert": "CA"})
        assert (new_certs / "client.crt").exists()
        assert (new_certs / "client.key").exists()
        assert (new_certs / "ca.crt").exists()


class TestGetPairingSecret:
    """Test get_pairing_secret method."""

    def test_returns_empty_when_no_file(self, pairing_mgr):
        assert pairing_mgr.get_pairing_secret() == ""

    def test_reads_stored_secret(self, pairing_mgr, data_dir):
        secret_path = data_dir / "certs" / "pairing_secret"
        secret_path.write_text("hex_secret_value\n")
        assert pairing_mgr.get_pairing_secret() == "hex_secret_value"


class TestDefaultCertsDir:
    """Test default certs_dir from environment."""

    def test_uses_env_var(self, tmp_path):
        config = MagicMock()
        config.camera_id = "cam-test"
        with patch.dict(os.environ, {"CAMERA_DATA_DIR": str(tmp_path)}):
            mgr = PairingManager(config)
        expected = os.path.join(str(tmp_path), "certs")
        assert mgr.client_cert_path == os.path.join(expected, "client.crt")


class TestPairingLifecycleState:
    """Test PAIRING state in CameraLifecycle."""

    def test_pairing_state_defined(self):
        from camera_streamer.lifecycle import State

        assert State.PAIRING == "pairing"

    @patch("camera_streamer.lifecycle.led")
    @patch("camera_streamer.lifecycle.LedController")
    @patch("camera_streamer.lifecycle.PairingManager")
    def test_skips_pairing_when_already_paired(self, MockPM, MockLed, mock_led):
        from camera_streamer.lifecycle import CameraLifecycle

        mock_pm = MagicMock()
        mock_pm.is_paired = True
        MockPM.return_value = mock_pm

        config = MagicMock(
            server_ip="192.168.1.100",
            camera_id="cam-test",
            is_configured=True,
        )
        platform = MagicMock(
            camera_device="/dev/video0",
            wifi_interface="wlan0",
            led_path=None,
            thermal_path=None,
            hostname_prefix="homecam",
        )

        lc = CameraLifecycle(config, platform, lambda: False)
        result = lc._do_pairing()
        assert result is True

    @patch("camera_streamer.lifecycle.led")
    @patch("camera_streamer.lifecycle.LedController")
    @patch("camera_streamer.lifecycle.PairingManager")
    def test_waits_for_pairing_then_succeeds(self, MockPM, MockLed, mock_led):
        from camera_streamer.lifecycle import CameraLifecycle

        mock_pm = MagicMock()
        # is_paired returns False first (initial check), then False (loop),
        # then True (loop check triggers exit)
        type(mock_pm).is_paired = PropertyMock(side_effect=[False, False, True])
        MockPM.return_value = mock_pm

        config = MagicMock(
            server_ip="192.168.1.100",
            camera_id="cam-test",
            is_configured=True,
        )
        platform = MagicMock(
            camera_device="/dev/video0",
            wifi_interface="wlan0",
            led_path=None,
            thermal_path=None,
            hostname_prefix="homecam",
        )

        lc = CameraLifecycle(config, platform, lambda: False)

        with patch("camera_streamer.lifecycle.time.sleep"):
            result = lc._do_pairing()
        assert result is True

    @patch("camera_streamer.lifecycle.led")
    @patch("camera_streamer.lifecycle.LedController")
    @patch("camera_streamer.lifecycle.PairingManager")
    def test_pairing_returns_false_on_shutdown(self, MockPM, MockLed, mock_led):
        from camera_streamer.lifecycle import CameraLifecycle

        mock_pm = MagicMock()
        mock_pm.is_paired = False
        MockPM.return_value = mock_pm

        config = MagicMock(
            server_ip="192.168.1.100",
            camera_id="cam-test",
            is_configured=True,
        )
        platform = MagicMock(
            camera_device="/dev/video0",
            wifi_interface="wlan0",
            led_path=None,
            thermal_path=None,
            hostname_prefix="homecam",
        )

        # Shutdown immediately
        lc = CameraLifecycle(config, platform, lambda: True)
        result = lc._do_pairing()
        assert result is False
