"""Unit tests for the camera-initiated unpair flow (ADR-0016 sync protocol).

Covers:
  * ``PairingManager.reset_local_state`` — wipes client cert, key, and
    pairing secret while leaving the CA cert intact so the next exchange
    with the same server can still TOFU-verify it.
  * ``PairingManager.send_goodbye`` — builds the HMAC-signed POST body,
    reports success on HTTP 200, and does not raise on transport / HTTP
    errors (unpair must continue locally regardless).
"""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from camera_streamer.pairing import PairingManager


def _make_config(camera_id="cam-abc123"):
    cfg = MagicMock()
    cfg.camera_id = camera_id
    return cfg


class TestResetLocalState:
    def test_removes_client_cert_key_and_secret(self, tmp_path):
        (tmp_path / "client.crt").write_text("CERT")
        (tmp_path / "client.key").write_text("KEY")
        (tmp_path / "pairing_secret").write_text("SECRET")
        (tmp_path / "ca.crt").write_text("CA")  # must survive

        pm = PairingManager(_make_config(), certs_dir=str(tmp_path))
        pm.reset_local_state()

        assert not (tmp_path / "client.crt").exists()
        assert not (tmp_path / "client.key").exists()
        assert not (tmp_path / "pairing_secret").exists()
        # CA cert is the TOFU anchor — must NOT be wiped
        assert (tmp_path / "ca.crt").read_text() == "CA"

    def test_is_idempotent(self, tmp_path):
        pm = PairingManager(_make_config(), certs_dir=str(tmp_path))
        pm.reset_local_state()  # nothing there, must not raise
        pm.reset_local_state()


class TestSendGoodbye:
    def _pm_with_certs(self, tmp_path, secret="ab" * 32):
        (tmp_path / "client.crt").write_text("CERT")
        (tmp_path / "client.key").write_text("KEY")
        (tmp_path / "pairing_secret").write_text(secret)
        return PairingManager(_make_config(), certs_dir=str(tmp_path))

    def test_skips_without_server_url(self, tmp_path):
        pm = self._pm_with_certs(tmp_path)
        ok, err = pm.send_goodbye("")
        assert ok is False
        assert "Server URL" in err

    def test_skips_without_secret(self, tmp_path):
        pm = PairingManager(_make_config(), certs_dir=str(tmp_path))
        ok, err = pm.send_goodbye("https://srv")
        assert ok is False
        assert "secret" in err.lower()

    def test_posts_signed_request(self, tmp_path):
        secret = "ab" * 32
        pm = self._pm_with_certs(tmp_path, secret=secret)

        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b""

        def _urlopen(req, context=None, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = req.data
            return _Resp()

        with (
            patch("camera_streamer.pairing.urllib.request.urlopen", _urlopen),
            patch("camera_streamer.pairing.ssl.SSLContext"),
        ):
            ok, err = pm.send_goodbye("https://srv")

        assert ok is True and err == ""
        assert captured["url"].endswith("/api/v1/cameras/goodbye")

        # Headers present
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        assert headers["x-camera-id"] == "cam-abc123"
        assert "x-timestamp" in headers
        assert "x-signature" in headers

        # Signature matches scheme: HMAC(secret, id:ts:sha256(body))
        body_hash = hashlib.sha256(captured["body"]).hexdigest()
        message = f"cam-abc123:{headers['x-timestamp']}:{body_hash}"
        expected = hmac.new(
            bytes.fromhex(secret), message.encode(), hashlib.sha256
        ).hexdigest()
        assert headers["x-signature"] == expected

        # Body contains camera_id
        assert json.loads(captured["body"])["camera_id"] == "cam-abc123"

    def test_reports_http_error_without_raising(self, tmp_path):
        import io
        import urllib.error

        pm = self._pm_with_certs(tmp_path)

        def _raise(*_a, **_kw):
            raise urllib.error.HTTPError(
                url="https://srv/",
                code=401,
                msg="err",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"Unknown camera"}'),
            )

        with (
            patch("camera_streamer.pairing.urllib.request.urlopen", _raise),
            patch("camera_streamer.pairing.ssl.SSLContext"),
        ):
            ok, err = pm.send_goodbye("https://srv")

        assert ok is False
        assert "401" in err

    def test_reports_network_error_without_raising(self, tmp_path):
        import urllib.error

        pm = self._pm_with_certs(tmp_path)

        def _raise(*_a, **_kw):
            raise urllib.error.URLError("unreachable")

        with (
            patch("camera_streamer.pairing.urllib.request.urlopen", _raise),
            patch("camera_streamer.pairing.ssl.SSLContext"),
        ):
            ok, err = pm.send_goodbye("https://srv")

        assert ok is False
        assert err  # non-empty message
