# REQ: SWR-011, SWR-065, SWR-066; RISK: RISK-007, RISK-015; SEC: SC-012, SC-020; TEST: TC-012, TC-054
"""Unit tests for the server-side encoder preset catalogue."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from monitor.services.camera_service import CameraService
from monitor.services.encoder_presets import (
    PRESET_PARAM_FIELDS,
    encoder_preset_params_match,
    filter_encoder_presets_for_camera,
    get_encoder_preset,
    list_encoder_presets,
)


def _camera(**overrides):
    defaults = {
        "width": 1920,
        "height": 1080,
        "fps": 25,
        "bitrate": 4000000,
        "h264_profile": "high",
        "keyframe_interval": 30,
        "sensor_modes": [],
        "encoder_max_pixels": 0,
        "encoder_preset": "",
        "name": "Front Door",
        "location": "Porch",
        "status": "online",
        "ip": "192.168.1.50",
        "recording_mode": "off",
        "resolution": "1080p",
        "paired_at": "",
        "last_seen": "2026-05-04T12:00:00Z",
        "firmware_version": "1.0.0",
        "rotation": 0,
        "hflip": False,
        "vflip": False,
        "recording_schedule": [],
        "recording_motion_enabled": False,
        "desired_stream_state": "stopped",
        "motion_sensitivity": 5,
        "config_sync": "unknown",
        "pending_config": {},
        "streaming": False,
        "cpu_temp": 0.0,
        "memory_percent": 0,
        "uptime_seconds": 0,
        "throttle_state": None,
        "image_controls": {},
        "image_quality": {},
        "offline_alerts_enabled": True,
        "notification_rule": {
            "enabled": True,
            "min_duration_seconds": 3,
            "coalesce_seconds": 60,
        },
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestEncoderPresetCatalogue:
    def test_exposes_shipping_presets_in_stable_order(self):
        presets = list_encoder_presets()
        assert [preset["key"] for preset in presets] == [
            "high_bitrate",
            "balanced",
            "low_bandwidth",
            "mobile_friendly",
        ]
        assert presets[1]["params"] == {
            "width": 1920,
            "height": 1080,
            "fps": 25,
            "bitrate": 4000000,
            "h264_profile": "high",
            "keyframe_interval": 30,
        }

    def test_matches_bundle_exactly(self):
        preset = get_encoder_preset("balanced")
        params = dict(preset["params"])
        assert encoder_preset_params_match(preset, params) is True
        params["bitrate"] = 3500000
        assert encoder_preset_params_match(preset, params) is False


class TestPresetFiltering:
    def test_missing_capabilities_uses_conservative_fallback(self):
        camera = _camera(sensor_modes=[], encoder_max_pixels=0)
        presets = filter_encoder_presets_for_camera(camera)
        assert [preset["key"] for preset in presets] == [
            "high_bitrate",
            "balanced",
            "low_bandwidth",
            "mobile_friendly",
        ]

    def test_filters_out_presets_above_mode_fps_or_encoder_limit(self):
        camera = _camera(
            sensor_modes=[{"width": 1280, "height": 720, "max_fps": 15.0}],
            encoder_max_pixels=1280 * 720,
        )
        presets = filter_encoder_presets_for_camera(camera)
        assert [preset["key"] for preset in presets] == ["low_bandwidth"]


class TestPresetValidationAgainstCameraService:
    @pytest.mark.parametrize("preset", list_encoder_presets())
    def test_catalogue_entries_validate_for_ov5647_baseline_sensor(self, preset):
        camera = _camera(
            sensor_modes=[
                {"width": 1280, "height": 720, "max_fps": 60.0},
                {"width": 1920, "height": 1080, "max_fps": 30.0},
            ],
            encoder_max_pixels=1920 * 1080,
        )
        store = MagicMock()
        store.get_camera.return_value = camera
        svc = CameraService(store)

        payload = dict(preset["params"])
        payload["encoder_preset"] = preset["key"]

        err, code = svc.update("cam-001", payload)
        assert code == 200, err
        assert camera.encoder_preset == preset["key"]

    def test_preset_field_bundle_is_the_single_source_of_truth(self):
        preset = get_encoder_preset("balanced")
        params = {field: preset["params"][field] for field in PRESET_PARAM_FIELDS}
        params["encoder_preset"] = "balanced"

        camera = _camera()
        store = MagicMock()
        store.get_camera.return_value = camera
        svc = CameraService(store)

        err, code = svc.update("cam-001", params)
        assert code == 200, err
        assert camera.encoder_preset == "balanced"
