"""
Sensor identification + per-sensor mode catalogue.

The home-monitor-camera image runs on a Pi Zero 2W with whichever
Pi-officially-supported camera sensor is on the CSI ribbon — OV5647
ZeroCam, IMX219 Module 2 / NoIR, IMX477 HQ, or IMX708 Module 3. Each
sensor has its own native resolution + framerate set, so the dashboard
Settings UI must render dropdowns specific to the connected sensor;
hardcoding a global table (the original design) silently locked all
cameras to OV5647-only modes.

This module is the single source of truth for "what sensor is plugged
in and what modes does it support". It feeds:

- ``ControlHandler.get_capabilities()`` — the camera's `/api/v1/capabilities`
  endpoint, which the heartbeat also embeds for the server's dashboard.
- ``ControlHandler._validate_params()`` — server-pushed config changes
  are validated against the actual sensor's modes, not a static table.

Detection strategy
==================

1. **Sensor model**: ``Picamera2.global_camera_info()[0]['Model']``.
   This is a class method that enumerates cameras via libcamera *without*
   acquiring an exclusive lock, so it can run in the same process as
   the streaming pipeline that opens ``Picamera2()`` later.

2. **Modes**: a hand-curated per-sensor table (``KNOWN_SENSOR_MODES``).
   Querying ``Picamera2().sensor_modes`` would require opening the
   camera, which conflicts with the streamer; the on-Pi data we'd get
   is also slightly noisy (libcamera-hello reports identical 30fps for
   every mode on the Zero 2W's clock-limited unicam path even when the
   sensor itself is faster). The hand-curated table reflects the actual
   max framerates each sensor supports per the Pi/Sony/OmniVision
   datasheets.

3. **Fallback**: if the camera is missing, libcamera enumeration fails,
   or the detected model is not in our known table, we return
   ``model=None`` and an empty mode list. Callers then fall back to a
   conservative default (the previous OV5647 table) so the camera is
   still controllable but the dashboard knows to show a "sensor not
   detected" hint.

Adding a new sensor
===================

1. Add the sensor's model string (lowercase, as libcamera reports it)
   to ``KNOWN_SENSOR_MODES`` with its mode list.
2. Add the ``.dtbo`` to ``RPI_KERNEL_DEVICETREE_OVERLAYS`` in
   ``meta-home-monitor/conf/machine/home-monitor-camera.conf``.
3. Add the sensor name to ``SUPPORTED_SENSORS`` in
   ``app/camera/config/ensure-camera-overlay.sh`` so the optional
   ``/data/config/camera-sensor`` override accepts it.

That's it — no Yocto kernel rebuild required (the driver and overlay
ship with meta-raspberrypi).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

log = logging.getLogger("camera-streamer.sensor_info")


# ---------------------------------------------------------------
# Wire shape — embedded in heartbeat payload + /api/v1/capabilities
# ---------------------------------------------------------------


@dataclass(frozen=True)
class SensorMode:
    """One supported (resolution, max framerate) pair."""

    width: int
    height: int
    max_fps: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SensorCapabilities:
    """Detected camera sensor + its supported modes.

    Fields:
        model: Lowercase libcamera model string (``"ov5647"``,
            ``"imx219"``, ``"imx477"``, ``"imx708"``), or ``None`` if
            no camera was detected. The wire format converts ``None``
            to JSON ``null``.
        modes: Sorted list of supported ``SensorMode`` records. Empty
            list when the sensor is unknown or absent.
        detection_method: Short tag explaining which detection path
            populated this record. One of ``"picamera2"`` (production),
            ``"injected"`` (tests), ``"fallback"`` (no camera or unknown
            sensor). Useful for server-side debugging.
    """

    model: str | None
    modes: tuple[SensorMode, ...] = field(default_factory=tuple)
    detection_method: str = "fallback"

    def to_dict(self) -> dict:
        return {
            "sensor_model": self.model,
            "sensor_modes": [m.to_dict() for m in self.modes],
            "detection_method": self.detection_method,
        }

    def display_name(self) -> str:
        """Sensor name in the casing the dashboard prefers."""
        if not self.model:
            return "Unknown"
        return self.model.upper()

    def valid_resolutions(self) -> set[tuple[int, int]]:
        """The set of ``(width, height)`` pairs the sensor supports."""
        return {(m.width, m.height) for m in self.modes}

    def max_fps_for(self, width: int, height: int) -> float | None:
        """Return the max framerate for one resolution, or ``None`` if unsupported."""
        for m in self.modes:
            if m.width == width and m.height == height:
                return m.max_fps
        return None


# ---------------------------------------------------------------
# Per-sensor known-modes table.
#
# Sourced from datasheets + on-device libcamera-hello validation:
#   - OV5647: ``ov5647_modes`` in libcamera + Pi Foundation docs
#   - IMX219: Sony IMX219 datasheet + Pi Foundation tuning
#   - IMX477: Sony IMX477 datasheet + Pi HQ camera docs
#   - IMX708: Sony IMX708 datasheet + Pi Camera Module 3 docs
#
# Max framerates are SENSOR-side; the actual achievable framerate also
# depends on the SoC's encoder. The Pi Zero 2W can hardware-encode
# ~30 fps at 1080p reliably; modes above that resolution are shown for
# capability discovery and stills, but streaming may fall back or fail
# at those rates. The dashboard surfaces this caveat.
# ---------------------------------------------------------------


KNOWN_SENSOR_MODES: dict[str, tuple[SensorMode, ...]] = {
    "ov5647": (
        SensorMode(640, 480, 58.0),
        SensorMode(1296, 972, 43.0),
        SensorMode(1920, 1080, 30.0),
        SensorMode(2592, 1944, 15.0),
    ),
    "imx219": (
        SensorMode(640, 480, 58.0),
        SensorMode(1640, 1232, 41.0),
        SensorMode(1920, 1080, 47.0),
        SensorMode(3280, 2464, 21.0),
    ),
    "imx477": (
        SensorMode(1332, 990, 120.0),
        SensorMode(2028, 1080, 50.0),
        SensorMode(2028, 1520, 40.0),
        SensorMode(4056, 3040, 10.0),
    ),
    "imx708": (
        SensorMode(1536, 864, 120.0),
        SensorMode(2304, 1296, 56.0),
        SensorMode(2304, 1296, 30.0),
        SensorMode(4608, 2592, 14.0),
    ),
}


# Conservative fallback when no sensor is detected (or detection fails
# entirely). Matches the legacy OV5647 hardcoded table so existing
# saved configurations (1920x1080 @ 30 fps) remain valid until the
# sensor is properly identified on the next boot.
FALLBACK_MODES: tuple[SensorMode, ...] = (
    SensorMode(640, 480, 58.0),
    SensorMode(1296, 972, 43.0),
    SensorMode(1920, 1080, 30.0),
)


# ---------------------------------------------------------------
# Detection
# ---------------------------------------------------------------


def detect_sensor_capabilities(
    *,
    global_info_factory=None,
) -> SensorCapabilities:
    """Identify the connected camera sensor and look up its modes.

    Production path: imports ``Picamera2`` lazily and calls
    ``Picamera2.global_camera_info()`` (a static method that enumerates
    cameras via libcamera without locking any of them — safe to call
    while the streaming pipeline owns the camera in another thread).

    Args:
        global_info_factory: Optional callable returning
            ``Picamera2.global_camera_info()``-shaped data
            (``[{"Model": "imx219", ...}, ...]``). Used by tests to
            avoid importing picamera2.

    Returns a ``SensorCapabilities`` with ``model=None`` and the
    fallback mode set when:
      - ``picamera2`` cannot be imported (test/CI host)
      - ``global_camera_info()`` returns an empty list (no sensor)
      - the detected model is not in ``KNOWN_SENSOR_MODES``

    Otherwise returns the recognised sensor's catalogued modes.
    """
    if global_info_factory is not None:
        info_list = _safe_call(global_info_factory)
    else:
        info_list = _picamera2_global_camera_info()

    if not info_list:
        log.info("No camera enumerated by libcamera — using fallback capabilities")
        return SensorCapabilities(
            model=None,
            modes=FALLBACK_MODES,
            detection_method="fallback",
        )

    raw_model = info_list[0].get("Model") or info_list[0].get("model")
    if not raw_model:
        log.warning(
            "libcamera entry has no Model key (got %r) — using fallback",
            info_list[0],
        )
        return SensorCapabilities(
            model=None,
            modes=FALLBACK_MODES,
            detection_method="fallback",
        )

    model = str(raw_model).strip().lower()
    modes = KNOWN_SENSOR_MODES.get(model)
    if modes is None:
        log.warning(
            "Detected sensor %r is not in KNOWN_SENSOR_MODES — using fallback",
            model,
        )
        return SensorCapabilities(
            model=model,
            modes=FALLBACK_MODES,
            detection_method="fallback",
        )

    log.info("Detected sensor %s with %d modes", model, len(modes))
    return SensorCapabilities(
        model=model,
        modes=modes,
        detection_method="picamera2",
    )


def _picamera2_global_camera_info():
    """Lazy import + call. Returns ``[]`` on any error."""
    try:
        from picamera2 import Picamera2  # type: ignore[import-not-found]
    except ImportError:
        log.debug("picamera2 not installed — sensor detection unavailable")
        return []
    return _safe_call(Picamera2.global_camera_info)


def _safe_call(factory):
    """Call ``factory()`` and coerce errors to ``[]``."""
    try:
        result = factory()
    except Exception as exc:
        # Defensive top-level catch: this runs at startup before any
        # streaming is set up, so any exception here would crash the
        # whole camera process. Log + fall back to empty enumeration
        # is far better than failing closed.
        log.warning("sensor enumeration failed: %s", exc)
        return []
    return list(result) if result else []


def capabilities_for_testing(
    model: str | None = "ov5647",
    modes: tuple[SensorMode, ...] | None = None,
) -> SensorCapabilities:
    """Build a ``SensorCapabilities`` directly for tests.

    Without arguments returns a known-good OV5647 capability set.
    Pass ``model="imx219"`` (etc.) for a different sensor.
    """
    if modes is None:
        if model and model in KNOWN_SENSOR_MODES:
            modes = KNOWN_SENSOR_MODES[model]
        else:
            modes = FALLBACK_MODES
    return SensorCapabilities(
        model=model,
        modes=modes,
        detection_method="injected",
    )
