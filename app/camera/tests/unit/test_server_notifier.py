"""Unit tests for camera server_notifier module."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from camera_streamer.server_notifier import (
    _build_signature,
    notify_config_change,
)


class TestBuildSignature:
    """Test HMAC-SHA256 signature computation."""

    def test_deterministic(self):
        secret = "ab" * 32
        sig1 = _build_signature(secret, "cam-01", "12345", b'{"fps":25}')
        sig2 = _build_signature(secret, "cam-01", "12345", b'{"fps":25}')
        assert sig1 == sig2

    def test_different_body_different_sig(self):
        secret = "ab" * 32
        sig1 = _build_signature(secret, "cam-01", "12345", b'{"fps":25}')
        sig2 = _build_signature(secret, "cam-01", "12345", b'{"fps":30}')
        assert sig1 != sig2

    def test_different_camera_different_sig(self):
        secret = "ab" * 32
        sig1 = _build_signature(secret, "cam-01", "12345", b'{"fps":25}')
        sig2 = _build_signature(secret, "cam-02", "12345", b'{"fps":25}')
        assert sig1 != sig2

    def test_different_timestamp_different_sig(self):
        secret = "ab" * 32
        sig1 = _build_signature(secret, "cam-01", "12345", b'{"fps":25}')
        sig2 = _build_signature(secret, "cam-01", "12346", b'{"fps":25}')
        assert sig1 != sig2

    def test_matches_manual_computation(self):
        secret = "ab" * 32
        body = b'{"fps":25}'
        camera_id = "cam-01"
        timestamp = "12345"

        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{camera_id}:{timestamp}:{body_hash}"
        expected = hmac.new(
            bytes.fromhex(secret), message.encode(), hashlib.sha256
        ).hexdigest()

        assert _build_signature(secret, camera_id, timestamp, body) == expected


class TestNotifyConfigChange:
    """Test fire-and-forget notification logic."""

    def test_skips_when_no_server_ip(self):
        config = MagicMock()
        config.server_ip = ""
        pairing = MagicMock()
        # Should not raise
        notify_config_change(config, pairing)
        pairing.get_pairing_secret.assert_not_called()

    def test_skips_when_no_pairing_secret(self):
        config = MagicMock()
        config.server_ip = "192.168.1.245"
        pairing = MagicMock()
        pairing.get_pairing_secret.return_value = ""
        notify_config_change(config, pairing)

    @staticmethod
    def _mock_config():
        """Create a mock config with stream properties."""
        cfg = MagicMock()
        cfg.server_ip = "192.168.1.245"
        cfg.camera_id = "cam-test01"
        cfg.certs_dir = "/tmp/certs"
        cfg.width = 1920
        cfg.height = 1080
        cfg.fps = 25
        cfg.bitrate = 4000000
        cfg.h264_profile = "high"
        cfg.keyframe_interval = 30
        cfg.rotation = 0
        cfg.hflip = False
        cfg.vflip = False
        return cfg

    @patch("camera_streamer.server_notifier.urllib.request.urlopen")
    def test_sends_correct_headers(self, mock_urlopen):
        config = self._mock_config()
        pairing = MagicMock()
        pairing.get_pairing_secret.return_value = "ab" * 32

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        notify_config_change(config, pairing)

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("X-camera-id") == "cam-test01"
        assert req.get_header("X-timestamp")
        assert req.get_header("X-signature")
        assert req.get_header("Content-type") == "application/json"
        assert req.get_method() == "POST"
        assert "192.168.1.245" in req.full_url

    @patch("camera_streamer.server_notifier.urllib.request.urlopen")
    def test_sends_stream_config_body(self, mock_urlopen):
        config = self._mock_config()
        pairing = MagicMock()
        pairing.get_pairing_secret.return_value = "ab" * 32

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        notify_config_change(config, pairing)

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        assert body["width"] == 1920
        assert body["height"] == 1080
        assert body["fps"] == 25

    @patch("camera_streamer.server_notifier.urllib.request.urlopen")
    def test_handles_http_error_gracefully(self, mock_urlopen):
        import urllib.error

        config = self._mock_config()
        pairing = MagicMock()
        pairing.get_pairing_secret.return_value = "ab" * 32

        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, None
        )
        # Should not raise
        notify_config_change(config, pairing)

    @patch("camera_streamer.server_notifier.urllib.request.urlopen")
    def test_handles_network_error_gracefully(self, mock_urlopen):
        import urllib.error

        config = self._mock_config()
        pairing = MagicMock()
        pairing.get_pairing_secret.return_value = "ab" * 32

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        # Should not raise
        notify_config_change(config, pairing)
