"""Unit tests for camera control handler (ADR-0015)."""

import time
from unittest.mock import MagicMock

import pytest

from camera_streamer.control import (
    SENSOR_MODES,
    ControlHandler,
    parse_control_request,
)


@pytest.fixture
def control(camera_config):
    """ControlHandler with mock stream manager."""
    stream = MagicMock()
    stream.is_streaming = True
    stream.consecutive_failures = 0
    stream.restart.return_value = True
    return ControlHandler(camera_config, stream)


@pytest.fixture
def control_no_stream(camera_config):
    """ControlHandler without stream manager."""
    return ControlHandler(camera_config, None)


# --- get_capabilities ---


class TestGetCapabilities:
    def test_returns_sensor_info(self, control):
        caps = control.get_capabilities()
        assert caps["sensor"] == "OV5647"
        assert len(caps["sensor_modes"]) == 4

    def test_sensor_modes_match_constants(self, control):
        caps = control.get_capabilities()
        for mode in caps["sensor_modes"]:
            key = (mode["width"], mode["height"])
            assert key in SENSOR_MODES
            assert mode["max_fps"] == SENSOR_MODES[key]

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
        # 2592x1944 max is 15 fps
        result, err, status = control.set_config(
            {"width": 2592, "height": 1944, "fps": 30}
        )
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
        """If changing to 2592x1944, fps must not exceed 15."""
        result, err, status = control.set_config(
            {"width": 2592, "height": 1944, "fps": 16}
        )
        assert status == 400
        assert "exceeds maximum 15" in err

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
