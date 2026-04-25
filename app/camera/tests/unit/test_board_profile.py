"""Unit tests for board profile detection + encoder ceiling.

The encoder ceiling is the safety rail that prevents the streamer
from offering a sensor mode the V4L2 H.264 hardware can't actually
allocate buffers for — the bug that bricked .115 when 8 MP IMX219
mode was selected. These tests pin the per-board ceilings and the
detection fallbacks.
"""

from __future__ import annotations

import pytest

from camera_streamer.board_profile import (
    BOARD_PROFILES,
    UNKNOWN_PROFILE,
    BoardProfile,
    get_board_profile,
)
from camera_streamer.sensor_info import (
    SensorMode,
    filter_modes_by_encoder,
)


class TestBoardLookup:
    @pytest.mark.parametrize(
        "model_string,expected_max_pixels",
        [
            ("Raspberry Pi Zero 2 W Rev 1.0", 2_100_000),
            ("Raspberry Pi 4 Model B Rev 1.5", 8_300_000),
            ("Raspberry Pi 5 Model B Rev 1.0", 8_300_000),
            ("Raspberry Pi 3 Model B Plus Rev 1.3", 2_100_000),
            ("Raspberry Pi Compute Module 4 Rev 1.0", 8_300_000),
        ],
    )
    def test_known_boards_match(self, model_string: str, expected_max_pixels: int):
        profile = get_board_profile(model_override=model_string)
        assert profile.max_encoder_pixels == expected_max_pixels

    def test_unknown_board_falls_back_conservative(self):
        profile = get_board_profile(model_override="Some Future Pi 99")
        assert profile is UNKNOWN_PROFILE
        # Conservative cap — must NOT silently allow 8 MP modes.
        assert profile.max_encoder_pixels < 4_000_000

    def test_empty_model_falls_back(self):
        profile = get_board_profile(model_override=None)
        # Without a model and no /proc/device-tree/model on the test
        # host, we end up with the unknown profile.
        assert profile.max_encoder_pixels < 4_000_000

    def test_profiles_have_sane_pixel_counts(self):
        # Sanity: every catalogued board's cap should at least allow
        # 1080p (2_073_600 pixels) — that's the minimum bar for
        # security streaming.
        for prefix, profile in BOARD_PROFILES.items():
            assert profile.max_encoder_pixels >= 2_073_600, (
                f"{prefix} cap {profile.max_encoder_pixels} too low for 1080p"
            )


class TestEncoderModeFilter:
    """The filter that bricks .115 if it ever ships back into prod."""

    def test_zero2w_rejects_imx219_8mp_mode(self):
        # IMX219 native 3280x2464 = 8_081_920 pixels. Zero 2W cap is
        # 2_100_000. The mode MUST be filtered out.
        modes = (
            SensorMode(640, 480, 58.0),
            SensorMode(1640, 1232, 41.0),
            SensorMode(1920, 1080, 47.0),
            SensorMode(3280, 2464, 21.0),
        )
        kept = filter_modes_by_encoder(modes, 2_100_000)
        kept_resolutions = {(m.width, m.height) for m in kept}
        assert (3280, 2464) not in kept_resolutions, (
            "8 MP IMX219 mode passed encoder filter on Zero-2W ceiling — "
            "would re-introduce the OOM-restart-loop bug"
        )
        assert (1640, 1232) in kept_resolutions, (
            "binned IMX219 mode incorrectly filtered out"
        )

    def test_pi4_keeps_8mp_mode(self):
        modes = (
            SensorMode(1640, 1232, 41.0),
            SensorMode(3280, 2464, 21.0),
        )
        kept = filter_modes_by_encoder(modes, 8_300_000)
        kept_resolutions = {(m.width, m.height) for m in kept}
        assert (3280, 2464) in kept_resolutions
        assert (1640, 1232) in kept_resolutions

    def test_filter_is_pure(self):
        """No side effects on the input tuple."""
        modes = (SensorMode(3280, 2464, 21.0),)
        before = list(modes)
        filter_modes_by_encoder(modes, 2_100_000)
        after = list(modes)
        assert before == after

    def test_empty_input_returns_empty(self):
        assert filter_modes_by_encoder((), 2_100_000) == ()

    def test_zero_ceiling_drops_everything(self):
        modes = (SensorMode(640, 480, 58.0),)
        assert filter_modes_by_encoder(modes, 0) == ()


class TestDetectionEnvOverride:
    def test_env_var_short_circuits_file_read(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HM_BOARD_MODEL", "Raspberry Pi 4 Model B")
        profile = get_board_profile()
        assert profile.max_encoder_pixels == 8_300_000


class TestBoardProfileImmutable:
    def test_frozen(self):
        bp = BoardProfile(name="x", max_encoder_pixels=1)
        with pytest.raises((AttributeError, Exception)):
            bp.max_encoder_pixels = 2  # type: ignore[misc]
