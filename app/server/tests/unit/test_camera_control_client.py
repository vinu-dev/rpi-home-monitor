# REQ: SWR-039, SWR-065, SWR-066; RISK: RISK-007, RISK-015; SEC: SC-002, SC-012; TEST: TC-037, TC-054
"""Unit tests for CameraControlClient (ADR-0015)."""

import pytest

from monitor.services.camera_control_client import CameraControlClient


@pytest.fixture
def client(data_dir):
    """CameraControlClient with test certs dir."""
    certs_dir = data_dir / "certs"
    return CameraControlClient(str(certs_dir))


class TestCameraControlClientUnit:
    """Unit tests that don't require a running server."""

    def test_init_stores_certs_dir(self, client, data_dir):
        assert client._certs_dir == str(data_dir / "certs")

    def test_ssl_context_no_certs(self, client):
        """SSL context works even without cert files."""
        ctx = client._ssl_context()
        assert ctx is not None

    def test_get_config_unreachable(self, client):
        """Returns error when camera is unreachable."""
        result, err = client.get_config("192.168.99.99")
        assert result is None
        assert "unreachable" in err.lower() or "refused" in err.lower() or err

    def test_set_config_unreachable(self, client):
        result, err = client.set_config("192.168.99.99", {"fps": 15})
        assert result is None
        assert err  # some error message

    def test_get_capabilities_unreachable(self, client):
        result, err = client.get_capabilities("192.168.99.99")
        assert result is None

    def test_get_status_unreachable(self, client):
        result, err = client.get_status("192.168.99.99")
        assert result is None

    def test_restart_stream_unreachable(self, client):
        result, err = client.restart_stream("192.168.99.99")
        assert result is None

    def test_start_stream_unreachable(self, client):
        """ADR-0017: stream/start returns error on unreachable camera."""
        result, err = client.start_stream("192.168.99.99")
        assert result is None
        assert err

    def test_stop_stream_unreachable(self, client):
        result, err = client.stop_stream("192.168.99.99")
        assert result is None
        assert err

    def test_get_stream_state_unreachable(self, client):
        result, err = client.get_stream_state("192.168.99.99")
        assert result is None
        assert err


class TestStreamControlEndpoints:
    """ADR-0017: start/stop/state use the correct camera paths."""

    def test_start_stream_uses_correct_path(self, client):
        from unittest.mock import patch

        with patch.object(
            client, "_request", return_value=({"state": "running"}, "")
        ) as m:
            result, err = client.start_stream("10.0.0.1")
        assert err == ""
        assert result == {"state": "running"}
        m.assert_called_once_with(
            "POST", "10.0.0.1", "/api/v1/control/stream/start", {}
        )

    def test_stop_stream_uses_correct_path(self, client):
        from unittest.mock import patch

        with patch.object(
            client, "_request", return_value=({"state": "stopped"}, "")
        ) as m:
            result, err = client.stop_stream("10.0.0.1")
        assert err == ""
        assert result == {"state": "stopped"}
        m.assert_called_once_with("POST", "10.0.0.1", "/api/v1/control/stream/stop", {})

    def test_get_stream_state_uses_correct_path(self, client):
        from unittest.mock import patch

        with patch.object(
            client, "_request", return_value=({"state": "running"}, "")
        ) as m:
            result, err = client.get_stream_state("10.0.0.1")
        assert err == ""
        assert result == {"state": "running"}
        m.assert_called_once_with("GET", "10.0.0.1", "/api/v1/control/stream/state")

    def test_start_stream_idempotent_already_running(self, client):
        """Camera reports already running → we surface success."""
        from unittest.mock import patch

        with patch.object(client, "_request", return_value=({"state": "running"}, "")):
            result, err = client.start_stream("10.0.0.1")
        assert err == ""
        assert result["state"] == "running"


class TestCameraServiceWithControl:
    """Test CameraService integration with control client."""

    def test_update_pushes_stream_params(self, app, cameras_json):
        """CameraService.update() pushes stream params to camera."""
        with app.app_context():
            from unittest.mock import MagicMock

            mock_control = MagicMock()
            mock_control.set_config.return_value = ({"applied": {"fps": 15}}, "")
            app.camera_service._control = mock_control

            err, status = app.camera_service.update(
                "cam-abc123",
                {"fps": 15},
                user="admin",
                ip="127.0.0.1",
            )
            assert status == 200
            assert err == ""
            mock_control.set_config.assert_called_once_with("192.168.1.50", {"fps": 15})

    def test_update_preset_pushes_one_bundle_without_echo_field(
        self, app, cameras_json
    ):
        with app.app_context():
            from unittest.mock import MagicMock

            mock_control = MagicMock()
            mock_control.set_config.return_value = (
                {"applied": {"width": 1920, "height": 1080}},
                "",
            )
            app.camera_service._control = mock_control

            err, status = app.camera_service.update(
                "cam-abc123",
                {
                    "width": 1920,
                    "height": 1080,
                    "fps": 25,
                    "bitrate": 4000000,
                    "h264_profile": "high",
                    "keyframe_interval": 30,
                    "encoder_preset": "balanced",
                },
                user="admin",
                ip="127.0.0.1",
            )
            assert status == 200
            assert err == ""
            mock_control.set_config.assert_called_once_with(
                "192.168.1.50",
                {
                    "width": 1920,
                    "height": 1080,
                    "fps": 25,
                    "bitrate": 4000000,
                    "h264_profile": "high",
                    "keyframe_interval": 30,
                },
            )

    def test_update_marks_pending_on_push_failure(self, app, cameras_json):
        """Config sync marked pending when push fails."""
        with app.app_context():
            from unittest.mock import MagicMock

            mock_control = MagicMock()
            mock_control.set_config.return_value = (None, "Camera unreachable")
            app.camera_service._control = mock_control

            app.camera_service.update(
                "cam-abc123",
                {"fps": 15},
                user="admin",
                ip="127.0.0.1",
            )

            camera = app.store.get_camera("cam-abc123")
            assert camera.config_sync == "pending"

    def test_update_marks_synced_on_push_success(self, app, cameras_json):
        """Config sync marked synced when push succeeds."""
        with app.app_context():
            from unittest.mock import MagicMock

            mock_control = MagicMock()
            mock_control.set_config.return_value = ({"applied": {"fps": 15}}, "")
            app.camera_service._control = mock_control

            app.camera_service.update(
                "cam-abc123",
                {"fps": 15},
                user="admin",
                ip="127.0.0.1",
            )

            camera = app.store.get_camera("cam-abc123")
            assert camera.config_sync == "synced"

    def test_update_non_stream_params_no_push(self, app, cameras_json):
        """Non-stream params (name, location) don't trigger push."""
        with app.app_context():
            from unittest.mock import MagicMock

            mock_control = MagicMock()
            app.camera_service._control = mock_control

            app.camera_service.update(
                "cam-abc123",
                {"name": "Back Yard"},
                user="admin",
                ip="127.0.0.1",
            )

            mock_control.set_config.assert_not_called()

    def test_update_validates_new_stream_fields(self, app, cameras_json):
        """New stream fields are validated."""
        with app.app_context():
            # Invalid bitrate
            err, status = app.camera_service.update(
                "cam-abc123",
                {"bitrate": 100},
                user="admin",
                ip="127.0.0.1",
            )
            assert status == 400
            assert "bitrate" in err

    def test_update_validates_rotation(self, app, cameras_json):
        with app.app_context():
            err, status = app.camera_service.update(
                "cam-abc123",
                {"rotation": 90},
                user="admin",
                ip="127.0.0.1",
            )
            assert status == 400
            assert "rotation" in err

    def test_update_validates_h264_profile(self, app, cameras_json):
        with app.app_context():
            err, status = app.camera_service.update(
                "cam-abc123",
                {"h264_profile": "ultra"},
                user="admin",
                ip="127.0.0.1",
            )
            assert status == 400
            assert "h264_profile" in err

    def test_update_validates_hflip_type(self, app, cameras_json):
        with app.app_context():
            err, status = app.camera_service.update(
                "cam-abc123",
                {"hflip": "yes"},
                user="admin",
                ip="127.0.0.1",
            )
            assert status == 400
            assert "hflip" in err


class TestCameraModelNewFields:
    """Test Camera model has new fields with correct defaults."""

    def test_default_stream_params(self, sample_camera):
        assert sample_camera.width == 1920
        assert sample_camera.height == 1080
        assert sample_camera.bitrate == 4000000
        assert sample_camera.h264_profile == "high"
        assert sample_camera.keyframe_interval == 30
        assert sample_camera.encoder_preset == ""
        assert sample_camera.rotation == 0
        assert sample_camera.hflip is False
        assert sample_camera.vflip is False
        assert sample_camera.config_sync == "unknown"

    def test_list_cameras_includes_stream_fields(self, app, cameras_json):
        """list_cameras response includes new stream fields."""
        with app.app_context():
            cameras = app.camera_service.list_cameras()
            cam = cameras[0]
            assert "width" in cam
            assert "height" in cam
            assert "bitrate" in cam
            assert "encoder_preset" in cam
            assert "config_sync" in cam

    def test_camera_status_includes_stream_fields(self, app, cameras_json):
        """get_camera_status response includes new stream fields."""
        with app.app_context():
            result, err = app.camera_service.get_camera_status("cam-abc123")
            assert err == ""
            assert "width" in result
            assert "encoder_preset" in result
            assert "config_sync" in result
