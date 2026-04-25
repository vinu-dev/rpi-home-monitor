"""Unit tests for sensor identification + per-sensor mode catalogue.

The detection function is the single source of truth for "which sensor
is connected and what modes does it support". It feeds the control
handler's capability response and (downstream) the dashboard Settings UI.

Tests verify:

1. Each known sensor in ``KNOWN_SENSOR_MODES`` is identified correctly
   from a libcamera-style ``[{"Model": "...", "Num": 0}]`` payload.
2. Unknown sensor models surface as the model name with the conservative
   fallback mode list, so the camera remains controllable.
3. Empty enumeration (no camera attached) returns ``model=None`` and
   the fallback modes — the caller treats this as "still booting".
4. ``Picamera2`` import failure (CI host without picamera2) collapses
   to the same fallback path, so the test suite passes on Linux/macOS/
   Windows without the dependency.
5. ``capabilities_for_testing`` is a clean injectable for downstream
   tests that need to drive sensor-specific behaviour.
"""

from __future__ import annotations

import pytest

from camera_streamer.sensor_info import (
    FALLBACK_MODES,
    KNOWN_SENSOR_MODES,
    SensorCapabilities,
    SensorMode,
    capabilities_for_testing,
    detect_sensor_capabilities,
)


def _info(model: str | None) -> list[dict]:
    """Build a libcamera-style global_camera_info payload."""
    if model is None:
        return []
    return [
        {
            "Id": "/base/soc/i2c0mux/i2c@1/imx219@10",
            "Location": 2,
            "Model": model,
            "Num": 0,
        }
    ]


class TestKnownSensors:
    @pytest.mark.parametrize(
        "model",
        sorted(KNOWN_SENSOR_MODES.keys()),
    )
    def test_each_known_sensor_round_trips(
        self, model: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HM_BOARD_MODEL", "Raspberry Pi 4 Model B")
        caps = detect_sensor_capabilities(global_info_factory=lambda: _info(model))
        assert caps.model == model
        # Kept modes must be a subset of the catalogue — the encoder
        # filter may drop modes whose pixel count exceeds the board's
        # H.264 ceiling (e.g. IMX477's 12 MP and IMX708's 12 MP native
        # modes on Pi 4's 4K cap).
        assert set(caps.modes).issubset(set(KNOWN_SENSOR_MODES[model]))
        assert len(caps.modes) > 0
        assert caps.detection_method == "picamera2"

    def test_uppercase_model_is_normalised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # libcamera typically reports lowercase, but be defensive.
        monkeypatch.setenv("HM_BOARD_MODEL", "Raspberry Pi 4 Model B")
        caps = detect_sensor_capabilities(global_info_factory=lambda: _info("IMX219"))
        assert caps.model == "imx219"
        assert caps.modes == KNOWN_SENSOR_MODES["imx219"]

    def test_whitespace_in_model_is_stripped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HM_BOARD_MODEL", "Raspberry Pi 4 Model B")
        caps = detect_sensor_capabilities(
            global_info_factory=lambda: _info("  ov5647  ")
        )
        assert caps.model == "ov5647"
        assert caps.modes == KNOWN_SENSOR_MODES["ov5647"]


class TestUnknownAndAbsent:
    def test_no_camera_returns_fallback(self) -> None:
        caps = detect_sensor_capabilities(global_info_factory=lambda: _info(None))
        assert caps.model is None
        assert caps.modes == FALLBACK_MODES
        assert caps.detection_method == "fallback"

    def test_unknown_model_keeps_name_and_uses_fallback_modes(self) -> None:
        caps = detect_sensor_capabilities(
            global_info_factory=lambda: _info("imx500")  # not in our table
        )
        assert caps.model == "imx500"
        assert caps.modes == FALLBACK_MODES
        assert caps.detection_method == "fallback"

    def test_missing_model_field_falls_back(self) -> None:
        caps = detect_sensor_capabilities(
            global_info_factory=lambda: [{"Id": "x", "Num": 0}]
        )
        assert caps.model is None
        assert caps.modes == FALLBACK_MODES

    def test_factory_exception_returns_fallback(self) -> None:
        def boom() -> list[dict]:
            raise RuntimeError("libcamera went away")

        caps = detect_sensor_capabilities(global_info_factory=boom)
        assert caps.model is None
        assert caps.modes == FALLBACK_MODES

    def test_default_path_when_picamera2_unavailable(self) -> None:
        """No factory passed → uses the production path. On CI hosts
        without picamera2 installed the import fails and the helper
        returns ``[]``, so we expect the fallback. (On a real Pi the
        live picamera2 path is exercised by integration tests.)"""
        caps = detect_sensor_capabilities()
        assert caps.modes == FALLBACK_MODES
        assert caps.detection_method == "fallback"


class TestSensorCapabilitiesShape:
    def test_to_dict_wire_shape(self) -> None:
        caps = SensorCapabilities(
            model="imx219",
            modes=(SensorMode(1920, 1080, 47.0),),
            detection_method="picamera2",
        )
        d = caps.to_dict()
        # Multi-sensor base shape.
        assert d["sensor_model"] == "imx219"
        assert d["sensor_modes"] == [{"width": 1920, "height": 1080, "max_fps": 47.0}]
        assert (
            d["sensor_detection_method"]
            if False
            else d["detection_method"] == "picamera2"
        )
        # New in #182: image_controls catalogue + encoder ceiling.
        assert "image_controls" in d
        assert "encoder_max_pixels" in d
        assert "board_name" in d

    def test_display_name_uppercases_known_model(self) -> None:
        caps = capabilities_for_testing("imx708")
        assert caps.display_name() == "IMX708"

    def test_display_name_for_unknown(self) -> None:
        caps = SensorCapabilities(model=None)
        assert caps.display_name() == "Unknown"

    def test_valid_resolutions(self) -> None:
        caps = capabilities_for_testing("imx219")
        # Must include native 8 MP IMX219 mode the user explicitly asked for.
        assert (3280, 2464) in caps.valid_resolutions()
        # Must NOT include OV5647-only 1296x972.
        assert (1296, 972) not in caps.valid_resolutions()

    def test_max_fps_for_known_resolution(self) -> None:
        caps = capabilities_for_testing("imx219")
        assert caps.max_fps_for(3280, 2464) == 21.0

    def test_max_fps_for_unknown_resolution(self) -> None:
        caps = capabilities_for_testing("ov5647")
        assert caps.max_fps_for(99, 99) is None


class TestCapabilitiesForTesting:
    """The injectable helper used by other test modules."""

    def test_default_is_ov5647(self) -> None:
        caps = capabilities_for_testing()
        assert caps.model == "ov5647"
        assert caps.modes == KNOWN_SENSOR_MODES["ov5647"]
        assert caps.detection_method == "injected"

    def test_named_sensor(self) -> None:
        caps = capabilities_for_testing("imx477")
        assert caps.model == "imx477"
        assert caps.modes == KNOWN_SENSOR_MODES["imx477"]

    def test_explicit_modes_override(self) -> None:
        custom = (SensorMode(123, 456, 7.0),)
        caps = capabilities_for_testing("ov5647", modes=custom)
        assert caps.modes == custom
