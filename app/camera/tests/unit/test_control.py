# REQ: SWR-048; RISK: RISK-009; SEC: SC-009; TEST: TC-045
"""Unit tests for camera control handler (ADR-0015)."""

import time
from unittest.mock import MagicMock

import pytest

from camera_streamer.control import (
    SENSOR_MODES,
    ControlHandler,
    parse_control_request,
)
from camera_streamer.sensor_info import (
    KNOWN_SENSOR_MODES,
    capabilities_for_testing,
)


@pytest.fixture
def control(camera_config):
    """ControlHandler with mock stream manager.

    Defaults to an injected OV5647 ``SensorCapabilities`` so the
    existing OV5647-shaped expectations (1920x1080 max 30 fps,
    1296x972 max 43, 640x480 max 58) hold without depending on
    real Picamera2 enumeration.
    """
    stream = MagicMock()
    stream.is_streaming = True
    stream.consecutive_failures = 0
    stream.restart.return_value = True
    return ControlHandler(
        camera_config,
        stream,
        sensor_capabilities=capabilities_for_testing("ov5647"),
    )


@pytest.fixture
def control_no_stream(camera_config):
    """ControlHandler without stream manager."""
    return ControlHandler(
        camera_config,
        None,
        sensor_capabilities=capabilities_for_testing("ov5647"),
    )


# --- get_capabilities ---


class TestGetCapabilities:
    def test_returns_sensor_info(self, control):
        caps = control.get_capabilities()
        assert caps["sensor"] == "OV5647"
        assert caps["sensor_model"] == "ov5647"
        # Catalogue ships four OV5647 modes (640x480, 1296x972,
        # 1920x1080, 2592x1944).
        assert len(caps["sensor_modes"]) == len(KNOWN_SENSOR_MODES["ov5647"])

    def test_sensor_modes_match_constants(self, control):
        caps = control.get_capabilities()
        for mode in caps["sensor_modes"]:
            key = (mode["width"], mode["height"])
            assert key in SENSOR_MODES
            assert mode["max_fps"] == SENSOR_MODES[key]

    def test_unknown_sensor_falls_back_to_default_modes(self, camera_config):
        """No sensor detected → handler still works; modes from FALLBACK_MODES."""
        from camera_streamer.sensor_info import FALLBACK_MODES, SensorCapabilities

        unknown = SensorCapabilities(
            model=None,
            modes=FALLBACK_MODES,
            detection_method="fallback",
        )
        h = ControlHandler(camera_config, None, sensor_capabilities=unknown)
        caps = h.get_capabilities()
        assert caps["sensor"] == "Unknown"
        assert caps["sensor_model"] is None
        assert len(caps["sensor_modes"]) == len(FALLBACK_MODES)
        assert caps["detection_method"] == "fallback"


class TestPerSensorCapabilities:
    """Each sensor reports its own catalogued modes; the dashboard
    Settings dropdown is built from these so cameras with different
    sensors render different option lists."""

    @pytest.mark.parametrize(
        "model,expected_top_resolution",
        [
            ("ov5647", (2592, 1944)),
            ("imx219", (3280, 2464)),
            ("imx477", (4056, 3040)),
            ("imx708", (4608, 2592)),
        ],
    )
    def test_native_resolution_present_in_capabilities(
        self, camera_config, model, expected_top_resolution
    ):
        h = ControlHandler(
            camera_config,
            None,
            sensor_capabilities=capabilities_for_testing(model),
        )
        caps = h.get_capabilities()
        resolutions = {(m["width"], m["height"]) for m in caps["sensor_modes"]}
        assert expected_top_resolution in resolutions, (
            f"{model} caps missing native resolution {expected_top_resolution}: {resolutions}"
        )
        assert caps["sensor"] == model.upper()
        assert caps["sensor_model"] == model

    def test_imx219_accepts_3280x2464_validation(self, camera_config):
        """User requirement: IMX219's 3280x2464 must be selectable."""
        h = ControlHandler(
            camera_config,
            None,
            sensor_capabilities=capabilities_for_testing("imx219"),
        )
        result, err, status = h.set_config(
            {"width": 3280, "height": 2464, "fps": 21},
            origin="server",
        )
        assert status == 200, err
        assert result["applied"]["width"] == 3280

    def test_ov5647_rejects_imx219_only_resolution(self, camera_config):
        """An OV5647 must NOT accept 3280x2464 — that's IMX219-only."""
        h = ControlHandler(
            camera_config,
            None,
            sensor_capabilities=capabilities_for_testing("ov5647"),
        )
        result, err, status = h.set_config({"width": 3280, "height": 2464})
        assert status == 400
        # Specific message identifies the rejected mode and lists valid
        # OV5647 modes.
        assert "Invalid resolution 3280x2464" in err or "must be one of" in err

    def test_parameters_list_all_params(self, control):
        caps = control.get_capabilities()
        params = caps["parameters"]
        expected = {
            "width",
            "height",
            "fps",
            "bitrate",
            "h264_profile",
            "keyframe_interval",
            "rotation",
            "hflip",
            "vflip",
            # ADR-0021: per-camera motion sensitivity dial (1-10)
            "motion_sensitivity",
            # MOTION_DETECTION on/off gate. Server pushes this
            # (renamed from recording_motion_enabled) when the admin
            # toggles motion on from the Camera Settings modal.
            "motion_detection",
        }
        assert set(params.keys()) == expected


# --- get_config ---


class TestGetConfig:
    def test_returns_current_values(self, control):
        cfg = control.get_config()
        assert cfg["width"] == 1920
        assert cfg["height"] == 1080
        assert cfg["fps"] == 25
        assert cfg["h264_profile"] == "high"

    def test_all_keys_present(self, control):
        cfg = control.get_config()
        expected = {
            "width",
            "height",
            "fps",
            "bitrate",
            "h264_profile",
            "keyframe_interval",
            "rotation",
            "hflip",
            "vflip",
            # ADR-0021: per-camera motion sensitivity dial (1-10)
            "motion_sensitivity",
            # MOTION_DETECTION on/off gate. Server pushes this
            # (renamed from recording_motion_enabled) when the admin
            # toggles motion on from the Camera Settings modal.
            "motion_detection",
            # #182 image-quality controls dict.
            "image_quality",
        }
        assert set(cfg.keys()) == expected


# --- set_config validation ---


class TestSetConfigValidation:
    def test_rejects_unknown_param(self, control):
        result, err, status = control.set_config({"unknown_field": 42})
        assert status == 400
        assert "Unknown parameters" in err

    def test_rejects_wrong_type_int(self, control):
        result, err, status = control.set_config({"fps": "fast"})
        assert status == 400
        assert "expected int" in err

    def test_rejects_wrong_type_bool(self, control):
        result, err, status = control.set_config({"hflip": "yes"})
        assert status == 400
        assert "expected bool" in err

    def test_rejects_invalid_resolution(self, control):
        result, err, status = control.set_config({"width": 1920, "height": 480})
        assert status == 400
        assert "Invalid resolution" in err

    def test_rejects_fps_exceeding_sensor_max(self, control):
        # 1920x1080 max is 30 fps
        result, err, status = control.set_config({"fps": 31})
        assert status == 400
        assert "exceeds maximum" in err

    def test_rejects_fps_below_min(self, control):
        result, err, status = control.set_config({"fps": 0})
        assert status == 400
        assert "minimum is 1" in err

    def test_rejects_fps_above_max(self, control):
        result, err, status = control.set_config({"fps": 100})
        assert status == 400
        assert "maximum is 58" in err

    def test_rejects_invalid_rotation(self, control):
        result, err, status = control.set_config({"rotation": 90})
        assert status == 400
        assert "must be one of" in err

    def test_rejects_invalid_h264_profile(self, control):
        result, err, status = control.set_config({"h264_profile": "ultra"})
        assert status == 400
        assert "must be one of" in err

    def test_rejects_bitrate_too_low(self, control):
        result, err, status = control.set_config({"bitrate": 100})
        assert status == 400
        assert "minimum is 500000" in err

    def test_rejects_bitrate_too_high(self, control):
        result, err, status = control.set_config({"bitrate": 99000000})
        assert status == 400
        assert "maximum is 8000000" in err

    def test_rejects_empty_params(self, control):
        result, err, status = control.set_config({})
        assert status == 400
        assert "No parameters" in err


# --- set_config success ---


class TestSetConfigApply:
    def test_applies_fps_change(self, control):
        result, err, status = control.set_config({"fps": 15})
        assert status == 200
        assert err == ""
        assert result["applied"]["fps"] == 15
        assert result["restart_required"] is True

    def test_applies_resolution_change(self, control):
        result, err, status = control.set_config({"width": 1296, "height": 972})
        assert status == 200
        assert result["applied"]["width"] == 1296
        assert result["applied"]["height"] == 972

    def test_no_change_returns_unchanged(self, control):
        # Config already has fps=25
        result, err, status = control.set_config({"fps": 25})
        assert status == 200
        assert result["status"] == "unchanged"
        assert result["applied"] == {}

    def test_restarts_stream(self, control):
        control.set_config({"fps": 15})
        control._stream.restart.assert_called_once()

    def test_persists_to_config(self, control, camera_config):
        control.set_config({"fps": 20})
        assert camera_config.fps == 20

    def test_applies_bool_param(self, control):
        result, err, status = control.set_config({"hflip": True})
        assert status == 200
        assert result["applied"]["hflip"] is True

    def test_applies_multiple_params(self, control):
        result, err, status = control.set_config(
            {"width": 640, "height": 480, "fps": 30, "bitrate": 2000000}
        )
        assert status == 200
        assert len(result["applied"]) == 4

    def test_no_stream_manager(self, control_no_stream):
        result, err, status = control_no_stream.set_config({"fps": 15})
        assert status == 200
        assert result["restarted"] is False

    def test_origin_defaults_to_server(self, control):
        result, err, status = control.set_config({"fps": 15})
        assert result["origin"] == "server"

    def test_origin_local(self, control):
        result, err, status = control.set_config({"fps": 15}, origin="local")
        assert result["origin"] == "local"

    def test_origin_server_explicit(self, control):
        result, err, status = control.set_config({"fps": 15}, origin="server")
        assert result["origin"] == "server"


# --- rate limiting ---


class TestRateLimiting:
    def test_rate_limits_rapid_changes(self, control):
        control.set_config({"fps": 15})
        result, err, status = control.set_config({"fps": 20})
        assert status == 429
        assert "Rate limited" in err

    def test_allows_after_cooldown(self, control):
        control.set_config({"fps": 15})
        # Manually reset the timer
        control._last_change_time = time.monotonic() - 10
        result, err, status = control.set_config({"fps": 20})
        assert status == 200


# --- replay protection ---


class TestReplayProtection:
    def test_rejects_stale_request_id(self, control):
        control.set_config({"fps": 15}, request_id=5)
        control._last_change_time = 0  # bypass rate limit
        result, err, status = control.set_config({"fps": 20}, request_id=3)
        assert status == 409
        assert "replay" in err.lower()

    def test_accepts_higher_request_id(self, control):
        control.set_config({"fps": 15}, request_id=5)
        control._last_change_time = 0
        result, err, status = control.set_config({"fps": 20}, request_id=6)
        assert status == 200


# --- get_status ---


class TestGetStatus:
    def test_returns_status(self, control):
        status = control.get_status()
        assert status["streaming"] is True
        assert status["consecutive_failures"] == 0
        assert "config" in status

    def test_no_stream_manager(self, control_no_stream):
        status = control_no_stream.get_status()
        assert status["streaming"] is False


# --- parse_control_request ---


class TestParseControlRequest:
    def test_valid_json(self):
        params, rid, err = parse_control_request(b'{"fps": 15}')
        assert err == ""
        assert params == {"fps": 15}
        assert rid == 0

    def test_with_request_id(self):
        params, rid, err = parse_control_request(b'{"fps": 15, "request_id": 42}')
        assert rid == 42
        assert "request_id" not in params

    def test_invalid_json(self):
        _, _, err = parse_control_request(b"not json")
        assert "Invalid JSON" in err

    def test_not_dict(self):
        _, _, err = parse_control_request(b"[1, 2, 3]")
        assert "Expected JSON object" in err

    def test_invalid_request_id_type(self):
        _, _, err = parse_control_request(b'{"request_id": "abc"}')
        assert "request_id must be integer" in err


# --- cross-field validation ---


class TestCrossFieldValidation:
    def test_fps_validated_against_new_resolution(self, control):
        """If changing to 1296x972, fps must not exceed 43."""
        result, err, status = control.set_config(
            {"width": 1296, "height": 972, "fps": 44}
        )
        assert status == 400
        assert "exceeds maximum 43" in err

    def test_fps_validated_against_current_resolution(self, control):
        """If only changing fps, validate against current resolution."""
        # Current is 1920x1080, max 30
        result, err, status = control.set_config({"fps": 31})
        assert status == 400
        assert "exceeds maximum 30" in err

    def test_valid_resolution_fps_combo(self, control):
        result, err, status = control.set_config(
            {"width": 640, "height": 480, "fps": 58}
        )
        assert status == 200


# --- stream state control (ADR-0017) ---


class FakeStreamManager:
    """Minimal stream manager test double — tracks start/stop invocations."""

    def __init__(self, is_streaming=False):
        self.is_streaming = is_streaming
        self.consecutive_failures = 0
        self.start_calls = 0
        self.stop_calls = 0

    def start(self):
        self.start_calls += 1
        self.is_streaming = True

    def stop(self):
        self.stop_calls += 1
        self.is_streaming = False

    def restart(self):
        return True


class TestStreamStateControl:
    def test_default_state_is_stopped_when_file_missing(self, camera_config, tmp_path):
        path = tmp_path / "stream_state"
        h = ControlHandler(camera_config, None, stream_state_path=str(path))
        assert h.desired_stream_state == "stopped"
        assert h.get_stream_state() == {"state": "stopped", "running": False}

    @pytest.mark.parametrize(
        "contents,expected",
        [
            ("running", "running"),
            ("stopped", "stopped"),
            ("garbage", "stopped"),
            ("", "stopped"),
            ("  running\n", "running"),
        ],
    )
    def test_reads_existing_state_file(
        self, camera_config, tmp_path, contents, expected
    ):
        path = tmp_path / "stream_state"
        path.write_text(contents)
        h = ControlHandler(camera_config, None, stream_state_path=str(path))
        assert h.desired_stream_state == expected

    def test_set_running_writes_file_and_starts_stream(self, camera_config, tmp_path):
        path = tmp_path / "stream_state"
        stream = FakeStreamManager(is_streaming=False)
        h = ControlHandler(camera_config, stream, stream_state_path=str(path))

        result, err, status = h.set_stream_state("running")

        assert status == 200 and err == ""
        assert result == {"state": "running", "running": True}
        assert path.read_text() == "running"
        assert stream.start_calls == 1
        assert stream.stop_calls == 0
        assert h.desired_stream_state == "running"

    def test_set_stopped_writes_file_and_stops_stream(self, camera_config, tmp_path):
        path = tmp_path / "stream_state"
        stream = FakeStreamManager(is_streaming=True)
        h = ControlHandler(camera_config, stream, stream_state_path=str(path))

        result, err, status = h.set_stream_state("stopped")

        assert status == 200 and err == ""
        assert result == {"state": "stopped", "running": False}
        assert path.read_text() == "stopped"
        assert stream.stop_calls == 1
        assert stream.start_calls == 0

    def test_idempotent_start_when_already_running(self, camera_config, tmp_path):
        path = tmp_path / "stream_state"
        stream = FakeStreamManager(is_streaming=True)
        h = ControlHandler(camera_config, stream, stream_state_path=str(path))

        h.set_stream_state("running")
        h.set_stream_state("running")

        # Never called start — the stream already reported running on both
        # calls — but both requests return 200 and persist the value.
        assert stream.start_calls == 0
        assert path.read_text() == "running"

    def test_idempotent_stop_when_already_stopped(self, camera_config, tmp_path):
        path = tmp_path / "stream_state"
        stream = FakeStreamManager(is_streaming=False)
        h = ControlHandler(camera_config, stream, stream_state_path=str(path))

        h.set_stream_state("stopped")
        h.set_stream_state("stopped")

        assert stream.stop_calls == 0

    def test_invalid_state_returns_400(self, camera_config, tmp_path):
        path = tmp_path / "stream_state"
        h = ControlHandler(camera_config, None, stream_state_path=str(path))

        result, err, status = h.set_stream_state("paused")

        assert status == 400
        assert result is None
        assert "running" in err and "stopped" in err
        assert not path.exists()

    def test_get_stream_state_shape(self, camera_config, tmp_path):
        path = tmp_path / "stream_state"
        path.write_text("running")
        stream = FakeStreamManager(is_streaming=True)
        h = ControlHandler(camera_config, stream, stream_state_path=str(path))

        state = h.get_stream_state()
        assert set(state.keys()) == {"state", "running"}
        assert state["state"] == "running"
        assert state["running"] is True

    def test_get_status_includes_desired_stream_state(self, camera_config, tmp_path):
        path = tmp_path / "stream_state"
        path.write_text("running")
        stream = FakeStreamManager(is_streaming=True)
        h = ControlHandler(camera_config, stream, stream_state_path=str(path))

        assert h.get_status()["desired_stream_state"] == "running"

    def test_creates_parent_dir(self, camera_config, tmp_path):
        path = tmp_path / "nested" / "dir" / "stream_state"
        h = ControlHandler(camera_config, None, stream_state_path=str(path))
        h.set_stream_state("running")
        assert path.read_text() == "running"
