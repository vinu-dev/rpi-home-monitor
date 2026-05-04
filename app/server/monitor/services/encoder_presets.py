# REQ: SWR-065, SWR-066; RISK: RISK-007, RISK-015; SEC: SC-012, SC-020; TEST: TC-054, TC-012
"""Server-side encoder preset catalogue and camera-fit helpers."""

from copy import deepcopy

PRESET_PARAM_FIELDS = (
    "width",
    "height",
    "fps",
    "bitrate",
    "h264_profile",
    "keyframe_interval",
)

# Pre-#173 cameras do not report sensor capabilities yet. Keep the preset
# list conservative for them: only the legacy 720p / 1080p combinations.
LEGACY_PRESET_RESOLUTIONS = {
    (1280, 720),
    (1920, 1080),
}

ENCODER_PRESETS = (
    {
        "key": "high_bitrate",
        "label": "High Bitrate",
        "description": "Best image quality. Uses the most LAN bandwidth and storage.",
        "min_sensor": "ov5647",
        "params": {
            "width": 1920,
            "height": 1080,
            "fps": 25,
            "bitrate": 6000000,
            "h264_profile": "high",
            "keyframe_interval": 50,
        },
    },
    {
        "key": "balanced",
        "label": "Balanced",
        "description": "Recommended. Good quality at moderate bandwidth.",
        "min_sensor": "ov5647",
        "params": {
            "width": 1920,
            "height": 1080,
            "fps": 25,
            "bitrate": 4000000,
            "h264_profile": "high",
            "keyframe_interval": 30,
        },
    },
    {
        "key": "low_bandwidth",
        "label": "Low Bandwidth",
        "description": "Lower resolution and frame rate for slow networks or limited storage.",
        "min_sensor": "ov5647",
        "params": {
            "width": 1280,
            "height": 720,
            "fps": 15,
            "bitrate": 1500000,
            "h264_profile": "main",
            "keyframe_interval": 30,
        },
    },
    {
        "key": "mobile_friendly",
        "label": "Mobile Friendly",
        "description": "Most compatible with phones and older browsers when viewing remotely.",
        "min_sensor": "ov5647",
        "params": {
            "width": 1280,
            "height": 720,
            "fps": 25,
            "bitrate": 2000000,
            "h264_profile": "baseline",
            "keyframe_interval": 25,
        },
    },
)

_PRESETS_BY_KEY = {preset["key"]: preset for preset in ENCODER_PRESETS}


def list_encoder_presets() -> list[dict]:
    """Return the catalogue in a JSON-safe order-preserving shape."""
    return deepcopy(list(ENCODER_PRESETS))


def get_encoder_preset(key: str) -> dict | None:
    """Return one preset by key."""
    preset = _PRESETS_BY_KEY.get(key)
    return deepcopy(preset) if preset is not None else None


def encoder_preset_params_match(
    preset: dict | None,
    params: dict,
) -> bool:
    """Does ``params`` exactly match ``preset``'s resolved stream fields?"""
    if preset is None:
        return False
    expected = preset["params"]
    return all(params.get(field) == expected[field] for field in PRESET_PARAM_FIELDS)


def filter_encoder_presets_for_camera(camera) -> list[dict]:
    """Return only presets that fit the camera's reported capabilities."""
    sensor_modes = list(getattr(camera, "sensor_modes", []) or [])
    if not sensor_modes:
        return [
            preset
            for preset in list_encoder_presets()
            if (preset["params"]["width"], preset["params"]["height"])
            in LEGACY_PRESET_RESOLUTIONS
        ]

    max_fps_by_resolution: dict[tuple[int, int], int] = {}
    for mode in sensor_modes:
        try:
            width = int(mode["width"])
            height = int(mode["height"])
            max_fps = int(mode["max_fps"])
        except (KeyError, TypeError, ValueError):
            continue
        if width <= 0 or height <= 0 or max_fps <= 0:
            continue
        key = (width, height)
        max_fps_by_resolution[key] = max(max_fps_by_resolution.get(key, 0), max_fps)

    encoder_max_pixels = int(getattr(camera, "encoder_max_pixels", 0) or 0)
    filtered: list[dict] = []
    for preset in list_encoder_presets():
        params = preset["params"]
        resolution = (params["width"], params["height"])
        max_fps = max_fps_by_resolution.get(resolution)
        if max_fps is None:
            continue
        if params["fps"] > max_fps:
            continue
        if (
            encoder_max_pixels
            and params["width"] * params["height"] > encoder_max_pixels
        ):
            continue
        filtered.append(preset)
    return filtered
