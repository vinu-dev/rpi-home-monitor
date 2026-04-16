"""Unit tests for the camera heartbeat sender module."""

import hashlib
import hmac
import json
import threading
from unittest.mock import MagicMock, patch

from camera_streamer.heartbeat import (
    HeartbeatSender,
    _build_signature,
    _get_cpu_temp,
    _get_memory_percent,
    _get_uptime_seconds,
)


# ---- Signature tests ----


class TestBuildSignature:
    """HMAC-SHA256 signing — same scheme as config-notify (ADR-0015)."""

    def test_deterministic(self):
        secret = "ab" * 32
        sig1 = _build_signature(secret, "cam-01", "123", b'{"streaming":true}')
        sig2 = _build_signature(secret, "cam-01", "123", b'{"streaming":true}')
        assert sig1 == sig2

    def test_different_body_different_sig(self):
        secret = "ab" * 32
        sig1 = _build_signature(secret, "cam-01", "123", b'{"streaming":true}')
        sig2 = _build_signature(secret, "cam-01", "123", b'{"streaming":false}')
        assert sig1 != sig2

    def test_different_camera_different_sig(self):
        secret = "ab" * 32
        sig1 = _build_signature(secret, "cam-01", "123", b"{}")
        sig2 = _build_signature(secret, "cam-02", "123", b"{}")
        assert sig1 != sig2

    def test_matches_manual_computation(self):
        secret = "ab" * 32
        body = b'{"streaming":true}'
        camera_id = "cam-01"
        timestamp = "99999"
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"{camera_id}:{timestamp}:{body_hash}"
        expected = hmac.new(
            bytes.fromhex(secret), message.encode(), hashlib.sha256
        ).hexdigest()
        assert _build_signature(secret, camera_id, timestamp, body) == expected


# ---- System metric helpers ----


class TestGetUptimeSeconds:
    def test_returns_int(self):
        with patch(
            "builtins.open",
            MagicMock(
                return_value=MagicMock(
                    __enter__=lambda s, *a: s,
                    __exit__=lambda s, *a: False,
                    read=lambda: "3600.42 1234.5\n",
                )
            ),
        ):
            result = _get_uptime_seconds()
            assert result == 3600

    def test_returns_zero_on_error(self):
        with patch("builtins.open", side_effect=OSError):
            assert _get_uptime_seconds() == 0


class TestGetMemoryPercent:
    def test_computes_percent(self):
        fake_meminfo = (
            "MemTotal:       2000 kB\n"
            "MemFree:         500 kB\n"
            "MemAvailable:    800 kB\n"
        )
        with patch(
            "builtins.open",
            MagicMock(
                return_value=MagicMock(
                    __enter__=lambda s, *a: s,
                    __exit__=lambda s, *a: False,
                    __iter__=lambda s: iter(fake_meminfo.splitlines(keepends=True)),
                )
            ),
        ):
            pct = _get_memory_percent()
            # (2000 - 800) / 2000 * 100 = 60%
            assert pct == 60

    def test_returns_zero_on_error(self):
        with patch("builtins.open", side_effect=OSError):
            assert _get_memory_percent() == 0


class TestGetCpuTemp:
    def test_reads_thermal_zone(self):
        with patch(
            "builtins.open",
            MagicMock(
                return_value=MagicMock(
                    __enter__=lambda s, *a: s,
                    __exit__=lambda s, *a: False,
                    read=lambda: "54321\n",
                )
            ),
        ):
            temp = _get_cpu_temp("/sys/class/thermal/thermal_zone0/temp")
            assert temp == 54.3

    def test_returns_zero_on_error(self):
        with patch("builtins.open", side_effect=OSError):
            assert _get_cpu_temp(None) == 0.0


# ---- HeartbeatSender ----


def _make_config(server_ip="192.168.1.1", camera_id="cam-001", paired=True):
    cfg = MagicMock()
    cfg.server_ip = server_ip
    cfg.camera_id = camera_id
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


def _make_pairing(secret="ab" * 32):
    p = MagicMock()
    p.get_pairing_secret.return_value = secret
    return p


class TestHeartbeatSender:
    """Test HeartbeatSender.send_once() and payload construction."""

    def test_skips_when_no_server_ip(self):
        cfg = _make_config(server_ip="")
        sender = HeartbeatSender(cfg, _make_pairing())
        result = sender.send_once()
        assert result is None

    def test_skips_when_no_pairing_secret(self):
        cfg = _make_config()
        pairing = MagicMock()
        pairing.get_pairing_secret.return_value = None
        sender = HeartbeatSender(cfg, pairing)
        result = sender.send_once()
        assert result is None

    def test_payload_contains_required_fields(self):
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing())
        payload = sender._build_payload()
        assert payload["camera_id"] == "cam-001"
        assert isinstance(payload["timestamp"], int)
        assert isinstance(payload["streaming"], bool)
        assert "cpu_temp" in payload
        assert "memory_percent" in payload
        assert "uptime_seconds" in payload
        sc = payload["stream_config"]
        assert sc["width"] == 1920
        assert sc["height"] == 1080
        assert sc["fps"] == 25

    def test_streaming_true_when_stream_manager_active(self):
        cfg = _make_config()
        stream = MagicMock()
        stream.is_streaming = True
        sender = HeartbeatSender(cfg, _make_pairing(), stream_manager=stream)
        payload = sender._build_payload()
        assert payload["streaming"] is True

    def test_streaming_false_when_no_stream_manager(self):
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing(), stream_manager=None)
        payload = sender._build_payload()
        assert payload["streaming"] is False

    def test_send_once_posts_with_hmac_headers(self):
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing())
        sent_headers = {}

        class FakeResp:
            status = 200

            def read(self):
                return b'{"ok":true}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def fake_urlopen(req, context=None, timeout=None):
            sent_headers.update(dict(req.headers))
            return FakeResp()

        with (
            patch("camera_streamer.heartbeat.ssl.SSLContext"),
            patch("camera_streamer.heartbeat.urllib.request.urlopen", fake_urlopen),
        ):
            result = sender.send_once()

        assert result == {"ok": True}
        assert (
            "X-camera-id" in sent_headers
            or "X-Camera-id" in sent_headers
            or any("camera-id" in k.lower() for k in sent_headers)
        )
        assert any("signature" in k.lower() for k in sent_headers)
        assert any("timestamp" in k.lower() for k in sent_headers)

    def test_send_once_returns_none_on_network_error(self):
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing())

        import urllib.error

        with (
            patch("camera_streamer.heartbeat.ssl.SSLContext"),
            patch(
                "camera_streamer.heartbeat.urllib.request.urlopen",
                side_effect=urllib.error.URLError("connection refused"),
            ),
        ):
            result = sender.send_once()

        assert result is None

    def test_apply_pending_config_calls_control_handler(self):
        cfg = _make_config()
        stream = MagicMock()
        sender = HeartbeatSender(cfg, _make_pairing(), stream_manager=stream)

        pending = {
            "width": 1280,
            "height": 720,
            "fps": 30,
            "bitrate": 2000000,
            "h264_profile": "main",
            "keyframe_interval": 30,
            "rotation": 0,
            "hflip": False,
            "vflip": False,
        }

        mock_handler = MagicMock()
        mock_handler.set_config.return_value = ({"applied": True}, "", 200)

        with (
            patch(
                "camera_streamer.heartbeat.parse_control_request",
                return_value=(pending, 0, ""),
            ),
            patch(
                "camera_streamer.heartbeat.ControlHandler", return_value=mock_handler
            ),
        ):
            sender._apply_pending_config(pending)

        mock_handler.set_config.assert_called_once()
        call_kwargs = mock_handler.set_config.call_args
        # Verify origin="server" is passed (prevents ping-pong)
        assert call_kwargs.kwargs.get("origin") == "server"

    def test_start_stop_thread(self):
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing())

        with patch.object(sender, "_send", return_value={"ok": True}):
            sender.start()
            assert sender._thread is not None
            assert sender._thread.is_alive()
            sender.stop()
            assert not sender._thread

    def test_start_is_idempotent(self):
        """Calling start() twice does not create two threads."""
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing())

        with patch.object(sender, "_send", return_value=None):
            sender.start()
            first_thread = sender._thread
            sender.start()
            assert sender._thread is first_thread
            sender.stop()
