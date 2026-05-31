# REQ: SWR-038; RISK: RISK-004; SEC: SC-003; TEST: TC-036
"""Unit tests for CameraOTAClient (ADR-0020)."""

import http.client
from unittest.mock import MagicMock, patch

import pytest

from monitor.services.camera_ota_client import (
    OTA_PORT,
    STATUS_PATH,
    UPLOAD_PATH,
    CameraOTAClient,
    _version_matches,
)


@pytest.fixture
def certs_dir(data_dir):
    """Directory with fake server.crt + server.key so _ssl_context() passes."""
    d = data_dir / "certs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "server.crt").write_text(
        "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"
    )
    (d / "server.key").write_text(
        "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n"
    )
    return d


@pytest.fixture
def client(certs_dir):
    return CameraOTAClient(str(certs_dir))


class TestInit:
    def test_stores_certs_dir(self, client, certs_dir):
        assert client._certs_dir == str(certs_dir)


class TestSSLContext:
    def test_raises_when_missing_certs(self, data_dir):
        c = CameraOTAClient(str(data_dir / "no-such-dir"))
        with pytest.raises(FileNotFoundError):
            c._ssl_context()

    def test_builds_context_with_certs(self, client):
        # load_cert_chain on a fake cert will raise ssl.SSLError —
        # it's enough to check we get past the FileNotFoundError path.
        with patch("monitor.services.camera_ota_client.ssl.SSLContext") as mock_ctx:
            client._ssl_context()
        mock_ctx.assert_called()


class TestPushBundle:
    def test_missing_bundle(self, client, tmp_path):
        ok, msg = client.push_bundle("10.0.0.1", str(tmp_path / "nope.swu"))
        assert not ok
        assert "not found" in msg.lower()

    def test_empty_bundle(self, client, tmp_path):
        p = tmp_path / "empty.swu"
        p.write_bytes(b"")
        ok, msg = client.push_bundle("10.0.0.1", str(p))
        assert not ok
        assert "empty" in msg.lower()

    def test_streams_upload_then_polls_until_installed(self, client, tmp_path):
        """Happy path: upload returns 202 Accepted, then the client
        polls /ota/status until the camera reports state=installed.
        """
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"x" * (512 * 1024 + 17))  # > one chunk to exercise loop

        sent_bytes = []

        def _progress(sent, total):
            sent_bytes.append((sent, total))

        # Upload POST returns 202, then the client polls get_status().
        # We mock get_status so the poll loop terminates quickly without
        # calling time.sleep for real (poll interval is 5s).
        upload_resp = MagicMock()
        upload_resp.status = 202
        upload_resp.read.return_value = (
            b'{"message": "Install triggered", "bundle_bytes": '
            + str(p.stat().st_size).encode()
            + b"}"
        )
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = upload_resp

        # First poll reports installing at 50, second reports installed.
        status_sequence = [
            ({"state": "installing", "progress": 50, "error": ""}, ""),
            ({"state": "installed", "progress": 100, "error": ""}, ""),
        ]

        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ) as mock_conn_cls,
            patch.object(client, "_ssl_context"),
            patch.object(client, "get_status", side_effect=status_sequence),
            patch("monitor.services.camera_ota_client.time.sleep"),
        ):
            ok, msg = client.push_bundle("10.0.0.1", str(p), progress_cb=_progress)

        assert ok is True
        assert msg == "Installed"
        # Progress fires during upload (0..total) and again while polling
        # (total+progress, total*2). Both phases must have contributed.
        total = 512 * 1024 + 17
        upload_phase = [s for s, t in sent_bytes if t == total]
        poll_phase = [s for s, t in sent_bytes if t == total * 2]
        assert upload_phase, "upload progress should have fired"
        assert poll_phase, "poll progress should have fired"
        assert upload_phase[-1] == total

        mock_conn_cls.assert_called_once()
        args, kwargs = mock_conn_cls.call_args
        assert args[0] == "10.0.0.1"
        assert args[1] == OTA_PORT
        fake_conn.putrequest.assert_called_with("POST", UPLOAD_PATH)
        fake_conn.close.assert_called_once()

    def test_http_error_returns_message(self, client, tmp_path):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")
        fake_resp = MagicMock()
        fake_resp.status = 400
        fake_resp.read.return_value = b'{"error": "bad sig"}'
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = fake_resp
        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
        ):
            ok, msg = client.push_bundle("10.0.0.1", str(p))
        assert ok is False
        assert msg == "bad sig"

    def test_oserror_returns_error(self, client, tmp_path):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")
        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                side_effect=OSError("connection refused"),
            ),
            patch.object(client, "_ssl_context"),
        ):
            ok, msg = client.push_bundle("10.0.0.1", str(p))
        assert ok is False
        assert "connection refused" in msg.lower()

    def test_preflight_busy_status_returns_meaningful_error(self, client, tmp_path):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")

        with (
            patch.object(
                client,
                "get_status",
                return_value=(
                    {
                        "state": "rebooting",
                        "progress": 100,
                        "current_version": "1.6.4",
                        "target_version": "1.6.5-dev",
                    },
                    "",
                ),
            ),
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection"
            ) as conn_cls,
        ):
            ok, msg = client.push_bundle(
                "10.0.0.1",
                str(p),
                expected_version="1.6.5-dev",
            )

        assert ok is False
        assert "Camera OTA is already in progress (rebooting, 100%)" in msg
        assert "current=1.6.4, target=1.6.5-dev" in msg
        conn_cls.assert_not_called()

    def test_preflight_skips_when_runtime_matches_build_profile_target(
        self, client, tmp_path
    ):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")

        with (
            patch.object(
                client,
                "get_status",
                return_value=(
                    {
                        "state": "idle",
                        "progress": 0,
                        "current_version": "1.6.5",
                    },
                    "",
                ),
            ),
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection"
            ) as conn_cls,
        ):
            ok, msg = client.push_bundle(
                "10.0.0.1",
                str(p),
                expected_version="v1.6.5-dev",
            )

        assert ok is True
        assert msg == "Installed"
        conn_cls.assert_not_called()

    def test_tls_setup_failure_returns_error(self, client, tmp_path):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")

        with patch.object(client, "_ssl_context", side_effect=OSError("bad cert")):
            ok, msg = client.push_bundle("10.0.0.1", str(p))

        assert ok is False
        assert "TLS setup failed" in msg

    def test_http_exception_returns_error_and_closes_connection(self, client, tmp_path):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")
        fake_conn = MagicMock()
        fake_conn.getresponse.side_effect = http.client.HTTPException("bad response")

        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
        ):
            ok, msg = client.push_bundle("10.0.0.1", str(p))

        assert ok is False
        assert "HTTP error" in msg
        fake_conn.close.assert_called_once()

    def test_http_error_uses_raw_body_when_json_decode_fails(self, client, tmp_path):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")
        fake_resp = MagicMock()
        fake_resp.status = 500
        fake_resp.read.return_value = b"plain failure"
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = fake_resp

        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
        ):
            ok, msg = client.push_bundle("10.0.0.1", str(p))

        assert ok is False
        assert msg == "plain failure"

    def test_polling_surfaces_install_reboot_and_final_version(self, client, tmp_path):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")
        statuses = []
        progress = []
        upload_resp = MagicMock()
        upload_resp.status = 202
        upload_resp.read.return_value = b"{}"
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = upload_resp
        status_sequence = [
            (
                {"state": "idle", "progress": 0, "current_version": "1.6.0"},
                "",
            ),
            (
                {
                    "state": "installing",
                    "progress": 40,
                    "protocol_version": 999,
                    "current_version": "1.6.0",
                },
                "",
            ),
            (None, "rebooting"),
            (
                {
                    "state": "installed",
                    "progress": 100,
                    "current_version": "1.6.0",
                },
                "",
            ),
            (
                {
                    "state": "validating",
                    "progress": 95,
                    "current_version": "v1.6.1-dev",
                },
                "",
            ),
        ]

        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
            patch.object(client, "get_status", side_effect=status_sequence),
            patch("monitor.services.camera_ota_client.time.sleep"),
        ):
            ok, msg = client.push_bundle(
                "10.0.0.1",
                str(p),
                expected_version="1.6.1-dev",
                status_cb=lambda state, pct, err="": statuses.append((state, pct, err)),
                progress_cb=lambda sent, total: progress.append((sent, total)),
            )

        assert ok is True
        assert msg == "Installed"
        assert ("installing", 68, "") in statuses
        assert ("rebooting", 90, "") in statuses
        assert ("rebooting", 95, "") in statuses
        assert statuses[-1] == ("installed", 100, "")
        assert progress

    def test_polling_returns_camera_error_and_ignores_callback_errors(
        self, client, tmp_path
    ):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")
        upload_resp = MagicMock()
        upload_resp.status = 202
        upload_resp.read.return_value = b"{}"
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = upload_resp

        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
            patch.object(
                client,
                "get_status",
                return_value=(
                    {"state": "error", "progress": 77, "error": "bad install"},
                    "",
                ),
            ),
            patch("monitor.services.camera_ota_client.time.sleep"),
        ):
            ok, msg = client.push_bundle(
                "10.0.0.1",
                str(p),
                progress_cb=lambda _sent, _total: (_ for _ in ()).throw(
                    RuntimeError("progress boom")
                ),
                status_cb=lambda _state, _pct, _err="": (_ for _ in ()).throw(
                    RuntimeError("status boom")
                ),
            )

        assert ok is False
        assert msg == "bad install"

    def test_confirms_expected_version_with_optional_v_prefix(self, client, tmp_path):
        p = tmp_path / "bundle.swu"
        p.write_bytes(b"data")
        upload_resp = MagicMock()
        upload_resp.status = 202
        upload_resp.read.return_value = b'{"message":"Install triggered"}'
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = upload_resp
        status_sequence = [
            (
                {"state": "idle", "progress": 0, "current_version": "1.6.0"},
                "",
            ),
            (
                {"state": "validating", "progress": 95, "current_version": "1.6.1-dev"},
                "",
            ),
        ]

        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
            patch.object(client, "get_status", side_effect=status_sequence),
            patch("monitor.services.camera_ota_client.time.sleep"),
        ):
            ok, msg = client.push_bundle(
                "10.0.0.1",
                str(p),
                expected_version="v1.6.1-dev",
            )

        assert ok is True
        assert msg == "Installed"


class TestGetStatus:
    def test_returns_camera_status(self, client):
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = b'{"state": "idle", "progress": 0, "error": ""}'
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = fake_resp
        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
        ):
            status, err = client.get_status("10.0.0.1")
        assert err == ""
        assert status["state"] == "idle"
        fake_conn.request.assert_called_with("GET", STATUS_PATH)

    def test_unreachable(self, client):
        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                side_effect=OSError("refused"),
            ),
            patch.object(client, "_ssl_context"),
        ):
            status, err = client.get_status("10.0.0.1")
        assert status is None
        assert "refused" in err.lower()

    def test_tls_setup_failure(self, client):
        with patch.object(client, "_ssl_context", side_effect=OSError("bad cert")):
            status, err = client.get_status("10.0.0.1")

        assert status is None
        assert "TLS setup failed" in err

    def test_non_success_response(self, client):
        fake_resp = MagicMock()
        fake_resp.status = 503
        fake_resp.read.return_value = b"busy"
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = fake_resp

        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
        ):
            status, err = client.get_status("10.0.0.1")

        assert status is None
        assert err == "HTTP 503"
        fake_conn.close.assert_called_once()

    def test_invalid_json_response(self, client):
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.read.return_value = b"{bad"
        fake_conn = MagicMock()
        fake_conn.getresponse.return_value = fake_resp

        with (
            patch(
                "monitor.services.camera_ota_client.http.client.HTTPSConnection",
                return_value=fake_conn,
            ),
            patch.object(client, "_ssl_context"),
        ):
            status, err = client.get_status("10.0.0.1")

        assert status is None
        assert "Invalid JSON" in err


def test_version_matches_accepts_equivalent_v_prefix_and_rejects_empty_expected():
    assert _version_matches("v1.6.1-dev", "1.6.1-dev") is True
    assert _version_matches("1.6.5", "v1.6.5-dev") is True
    assert _version_matches("1.6.5", "1.6.5-prod") is True
    assert _version_matches("1.6.5-dev.20260531", "1.6.5") is False
    assert _version_matches("1.6.4", "v1.6.5-dev") is False
    assert _version_matches("1.6.1", "") is False
