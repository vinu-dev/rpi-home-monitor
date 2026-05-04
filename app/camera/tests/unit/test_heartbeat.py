# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Unit tests for the camera heartbeat sender module."""

import hashlib
import hmac
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
        assert "throttle_state" in payload
        sc = payload["stream_config"]
        assert sc["width"] == 1920
        assert sc["height"] == 1080
        assert sc["fps"] == 25

    def test_payload_includes_throttle_state(self):
        cfg = _make_config()
        sender = HeartbeatSender(
            cfg,
            _make_pairing(),
            vcgencmd_path="/usr/bin/vcgencmd",
        )
        throttle = {
            "under_voltage_now": True,
            "under_voltage_sticky": True,
            "frequency_capped_now": False,
            "frequency_capped_sticky": False,
            "throttled_now": False,
            "throttled_sticky": False,
            "soft_temp_limit_now": False,
            "soft_temp_limit_sticky": False,
            "last_updated": "2026-05-04T00:00:00Z",
            "raw_value_hex": "0x00010001",
            "source": "vcgencmd",
        }
        with patch(
            "camera_streamer.heartbeat._read_throttle_state", return_value=throttle
        ):
            payload = sender._build_payload()
        assert payload["throttle_state"] == throttle

    def test_throttle_state_retains_last_good_sample(self):
        cfg = _make_config()
        sender = HeartbeatSender(
            cfg, _make_pairing(), vcgencmd_path="/usr/bin/vcgencmd"
        )
        throttle = {
            "under_voltage_now": False,
            "under_voltage_sticky": True,
            "frequency_capped_now": False,
            "frequency_capped_sticky": False,
            "throttled_now": False,
            "throttled_sticky": False,
            "soft_temp_limit_now": False,
            "soft_temp_limit_sticky": False,
            "last_updated": "2026-05-04T00:00:00Z",
            "raw_value_hex": "0x00010000",
            "source": "vcgencmd",
        }
        with patch(
            "camera_streamer.heartbeat._read_throttle_state",
            side_effect=[throttle, None],
        ):
            assert sender._build_payload()["throttle_state"] == throttle
            assert sender._build_payload()["throttle_state"] == throttle

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

    def test_stream_state_fallback_without_control_handler(self):
        """Without a handler, stream_state mirrors the streaming flag."""
        cfg = _make_config()
        stream = MagicMock()
        stream.is_streaming = True
        sender = HeartbeatSender(cfg, _make_pairing(), stream_manager=stream)
        payload = sender._build_payload()
        assert payload["stream_state"] == "running"

        stream.is_streaming = False
        payload = sender._build_payload()
        assert payload["stream_state"] == "stopped"

    def test_hardware_ok_defaults_true_without_capture_manager(self):
        """When no CaptureManager is wired, hardware is assumed OK.

        Prevents test stubs from lighting up a false "no camera
        module" warning on the dashboard.
        """
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing(), capture_manager=None)
        payload = sender._build_payload()
        assert payload["hardware_ok"] is True
        assert payload["hardware_error"] == ""

    def test_hardware_fields_reflect_capture_manager_state(self):
        """Heartbeat mirrors CaptureManager.available + last_error."""
        cfg = _make_config()
        capture = MagicMock()
        capture.available = False
        capture.last_error = "No camera module detected."
        sender = HeartbeatSender(cfg, _make_pairing(), capture_manager=capture)
        payload = sender._build_payload()
        assert payload["hardware_ok"] is False
        assert payload["hardware_error"] == "No camera module detected."

        # When hardware recovers, heartbeat clears the banner fields.
        capture.available = True
        capture.last_error = ""
        payload = sender._build_payload()
        assert payload["hardware_ok"] is True
        assert payload["hardware_error"] == ""

    def test_stream_state_from_control_handler(self):
        """ADR-0017: with a handler, stream_state is the persisted desired value.

        The desired state can diverge from the live streaming flag (e.g. the
        pipeline crashed while desired=running) — the server needs the
        *desired* value to detect drift and take corrective action.
        """
        cfg = _make_config()
        stream = MagicMock()
        stream.is_streaming = False
        handler = MagicMock()
        handler.desired_stream_state = "running"
        sender = HeartbeatSender(
            cfg,
            _make_pairing(),
            stream_manager=stream,
            control_handler=handler,
        )
        payload = sender._build_payload()
        assert payload["stream_state"] == "running"
        # The legacy "streaming" field still reflects the live pipeline
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
            # Stub the new server-notify path so the test doesn't try
            # to reach the network — that's covered by dedicated tests
            # below.
            patch("camera_streamer.heartbeat.notify_config_change"),
        ):
            sender._apply_pending_config(pending)

        mock_handler.set_config.assert_called_once()
        call_kwargs = mock_handler.set_config.call_args
        # Verify origin="server" is passed (prevents ping-pong)
        assert call_kwargs.kwargs.get("origin") == "server"

    def test_apply_pending_config_notifies_server_on_success(self):
        """After a successful apply, the camera must POST /config-notify so
        the server can mark config_sync=synced — this is the explicit ack
        that breaks the stuck-pending loop in #231 from the camera side."""
        cfg = _make_config()
        stream = MagicMock()
        pairing = _make_pairing()
        sender = HeartbeatSender(cfg, pairing, stream_manager=stream)

        pending = {"fps": 30}

        mock_handler = MagicMock()
        mock_handler.set_config.return_value = ({"applied": True}, "", 200)

        # Capture the thread args so we can assert what would have been
        # sent without actually starting a real thread.
        captured = {}

        def fake_thread(**kwargs):
            captured["target"] = kwargs.get("target")
            captured["args"] = kwargs.get("args")
            captured["name"] = kwargs.get("name")
            captured["daemon"] = kwargs.get("daemon")
            t = MagicMock()
            t.start = MagicMock()
            return t

        with (
            patch(
                "camera_streamer.heartbeat.parse_control_request",
                return_value=(pending, 0, ""),
            ),
            patch(
                "camera_streamer.heartbeat.ControlHandler", return_value=mock_handler
            ),
            patch("camera_streamer.heartbeat.notify_config_change") as mock_notify,
            patch(
                "camera_streamer.heartbeat.threading.Thread",
                side_effect=fake_thread,
            ),
        ):
            sender._apply_pending_config(pending)

        # The notify thread was scheduled with the right callable + args.
        assert captured["target"] is mock_notify
        assert captured["args"] == (cfg, pairing)
        assert captured["daemon"] is True
        assert "config-notify" in captured["name"]

    def test_apply_pending_config_does_not_notify_on_failure(self):
        """If set_config returned an error, the apply did NOT land —
        notifying the server would falsely advance config_sync to synced
        on a failed push."""
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing())

        pending = {"fps": 30}

        mock_handler = MagicMock()
        # Non-empty error string from set_config means apply failed.
        mock_handler.set_config.return_value = (None, "rejected: bad fps", 400)

        with (
            patch(
                "camera_streamer.heartbeat.parse_control_request",
                return_value=(pending, 0, ""),
            ),
            patch(
                "camera_streamer.heartbeat.ControlHandler", return_value=mock_handler
            ),
            patch("camera_streamer.heartbeat.notify_config_change") as mock_notify,
            patch("camera_streamer.heartbeat.threading.Thread") as mock_thread_cls,
        ):
            sender._apply_pending_config(pending)

        # No notify thread spawned, no notify called.
        mock_thread_cls.assert_not_called()
        mock_notify.assert_not_called()

    def test_apply_pending_config_does_not_notify_on_parse_error(self):
        """A malformed pending payload short-circuits before set_config —
        the notify path must not fire on this branch either."""
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing())

        with (
            patch(
                "camera_streamer.heartbeat.parse_control_request",
                return_value=(None, 0, "schema mismatch"),
            ),
            patch("camera_streamer.heartbeat.ControlHandler") as mock_handler_cls,
            patch("camera_streamer.heartbeat.threading.Thread") as mock_thread_cls,
        ):
            sender._apply_pending_config({"fps": "garbage"})

        mock_handler_cls.assert_not_called()
        mock_thread_cls.assert_not_called()

    def test_notify_thread_failure_does_not_propagate(self):
        """Threading.Thread itself raising must not break the apply path —
        the camera should log and continue, not crash the heartbeat loop."""
        cfg = _make_config()
        sender = HeartbeatSender(cfg, _make_pairing())

        mock_handler = MagicMock()
        mock_handler.set_config.return_value = ({"applied": True}, "", 200)

        with (
            patch(
                "camera_streamer.heartbeat.parse_control_request",
                return_value=({"fps": 30}, 0, ""),
            ),
            patch(
                "camera_streamer.heartbeat.ControlHandler", return_value=mock_handler
            ),
            patch(
                "camera_streamer.heartbeat.threading.Thread",
                side_effect=RuntimeError("can't allocate thread"),
            ),
        ):
            # Must not raise
            sender._apply_pending_config({"fps": 30})

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


class TestHeartbeatUnpairDetection:
    """Server-side unpair detection — 401 Unknown camera threshold logic."""

    def _http_error(self, code, body_text):
        """Build a urllib HTTPError with a readable body."""
        import io
        import urllib.error

        return urllib.error.HTTPError(
            url="https://srv/",
            code=code,
            msg="err",
            hdrs=None,
            fp=io.BytesIO(body_text.encode()),
        )

    def test_401_unknown_camera_counts_increment(self, tmp_path):
        cfg = _make_config()
        cfg.certs_dir = str(tmp_path)
        sender = HeartbeatSender(cfg, _make_pairing())

        err = self._http_error(401, '{"error": "Unknown camera"}')
        with (
            patch("camera_streamer.heartbeat.ssl.SSLContext"),
            patch("camera_streamer.heartbeat.urllib.request.urlopen", side_effect=err),
        ):
            sender.send_once()
        assert sender._consecutive_unknown_camera == 1

    def test_successful_heartbeat_resets_counter(self, tmp_path):
        cfg = _make_config()
        cfg.certs_dir = str(tmp_path)
        sender = HeartbeatSender(cfg, _make_pairing())
        sender._consecutive_unknown_camera = 3

        resp = MagicMock()
        resp.read.return_value = b"{}"
        resp.status = 200
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: False

        with (
            patch("camera_streamer.heartbeat.ssl.SSLContext"),
            patch(
                "camera_streamer.heartbeat.urllib.request.urlopen", return_value=resp
            ),
        ):
            sender.send_once()
        assert sender._consecutive_unknown_camera == 0

    def test_other_401_does_not_trigger_unpair(self, tmp_path):
        """A 401 without 'Unknown camera' (e.g. replay, bad signature) must not unpair us."""
        cfg = _make_config()
        cfg.certs_dir = str(tmp_path)
        sender = HeartbeatSender(cfg, _make_pairing())

        err = self._http_error(401, '{"error": "Invalid signature"}')
        with (
            patch("camera_streamer.heartbeat.ssl.SSLContext"),
            patch("camera_streamer.heartbeat.urllib.request.urlopen", side_effect=err),
        ):
            sender.send_once()
        assert sender._consecutive_unknown_camera == 0

    def test_network_error_does_not_count_as_unpair(self, tmp_path):
        import urllib.error

        cfg = _make_config()
        cfg.certs_dir = str(tmp_path)
        sender = HeartbeatSender(cfg, _make_pairing())

        with (
            patch("camera_streamer.heartbeat.ssl.SSLContext"),
            patch(
                "camera_streamer.heartbeat.urllib.request.urlopen",
                side_effect=urllib.error.URLError("unreachable"),
            ),
        ):
            sender.send_once()
        assert sender._consecutive_unknown_camera == 0

    def test_threshold_triggers_wipe_and_restart(self, tmp_path):
        """After UNPAIR_401_THRESHOLD consecutive Unknown-camera responses we wipe
        local certs and send SIGTERM to self so systemd restarts us."""
        from camera_streamer.heartbeat import UNPAIR_401_THRESHOLD

        cfg = _make_config()
        cfg.certs_dir = str(tmp_path)
        # Seed the certs we expect to be removed
        (tmp_path / "client.crt").write_text("CERT")
        (tmp_path / "client.key").write_text("KEY")
        (tmp_path / "pairing_secret").write_text("SECRET")

        sender = HeartbeatSender(cfg, _make_pairing())
        kill_calls = []

        # Build a fresh HTTPError for every call — the body BytesIO is
        # exhausted after a single read().
        def _fresh_err(*_a, **_kw):
            raise self._http_error(401, '{"error": "Unknown camera"}')

        def _fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        with (
            patch("camera_streamer.heartbeat.ssl.SSLContext"),
            patch(
                "camera_streamer.heartbeat.urllib.request.urlopen",
                side_effect=_fresh_err,
            ),
            patch("camera_streamer.heartbeat.os.kill", _fake_kill),
        ):
            for _ in range(UNPAIR_401_THRESHOLD):
                sender.send_once()

        # Certs gone, SIGTERM sent to self, stop flag set
        import os
        import signal

        assert not (tmp_path / "client.crt").exists()
        assert not (tmp_path / "client.key").exists()
        assert not (tmp_path / "pairing_secret").exists()
        assert any(
            pid == os.getpid() and sig == signal.SIGTERM for pid, sig in kill_calls
        )
        assert sender._stop_event.is_set()
