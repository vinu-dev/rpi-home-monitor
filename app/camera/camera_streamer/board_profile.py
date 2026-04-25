"""
Hardware-aware encoder limits per Pi board.

Sensors advertise modes the silicon can capture, but the SoC's H.264
encoder has its own ceiling. The Pi Zero 2W's V4L2 H.264 encoder
cannot allocate buffers for 3280x2464 (8 MP) frames — the kernel
returns ``OSError: [Errno 12] Cannot allocate memory`` and the
streamer enters a restart loop. This module identifies the running
board and returns its encoder ceiling so ``sensor_info`` can filter
``sensor_modes`` to a list that's actually usable end-to-end.

Detection order:

1. ``/proc/device-tree/model`` — null-terminated string, set by
   firmware. The canonical lookup on Raspberry Pi OS / Yocto.
2. Fallback to a ``BOARD_PROFILE`` env var override (test hosts).
3. Conservative fallback to ``UNKNOWN`` (~2 MP cap) — keeps a future
   board working with safe defaults until a profile is added.

Adding a new board: append an entry to ``BOARD_PROFILES`` keyed by
the prefix that appears in ``/proc/device-tree/model`` (case-insensitive
substring match).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

log = logging.getLogger("camera-streamer.board_profile")


@dataclass(frozen=True)
class BoardProfile:
    """Per-board hardware ceiling for the Pi's V4L2 H.264 encoder.

    Fields:
        name: Short human label (e.g. ``"Raspberry Pi Zero 2 W"``).
        max_encoder_pixels: Hard ceiling on width*height for any
            H.264 encode session. Determined empirically from the
            board's CMA/encoder buffer headroom — values above this
            cause ``VIDIOC_REQBUFS`` to fail with ENOMEM.
    """

    name: str
    max_encoder_pixels: int


# Profiles keyed by a substring that appears in
# /proc/device-tree/model. Match is case-insensitive.
#
# Encoder pixel ceilings (empirical):
#   Zero 2W: 1920*1080 = 2_073_600. The 1640x1232 binned IMX219 mode
#       (2_020_480 px) fits. The 3280x2464 mode (8_081_920 px) does
#       NOT — confirmed live: VIDIOC_REQBUFS → ENOMEM.
#   Pi 4B:  4K (3840*2160 = 8_294_400) is the documented ceiling for
#       hardware H.264. We're being conservative and capping at the
#       same number.
#   Pi 5:   The Pi 5 has no dedicated H.264 encoder (relies on CPU
#       software encode); ceiling is CPU-bound rather than ENOMEM.
#       Set high enough to not be the limiter; software-encode
#       performance cliff comes well before this.
BOARD_PROFILES: dict[str, BoardProfile] = {
    "raspberry pi zero 2": BoardProfile(
        name="Raspberry Pi Zero 2 W",
        max_encoder_pixels=2_100_000,
    ),
    "raspberry pi 4": BoardProfile(
        name="Raspberry Pi 4 Model B",
        max_encoder_pixels=8_300_000,
    ),
    "raspberry pi 5": BoardProfile(
        name="Raspberry Pi 5",
        max_encoder_pixels=8_300_000,
    ),
    "raspberry pi 3": BoardProfile(
        name="Raspberry Pi 3",
        max_encoder_pixels=2_100_000,
    ),
    "raspberry pi compute module": BoardProfile(
        name="Raspberry Pi Compute Module",
        max_encoder_pixels=8_300_000,
    ),
}


# Fallback for an unrecognised board. Conservative ~2 MP cap so a
# new SoC doesn't accidentally let a too-large mode through and
# trigger the same OOM the Zero 2W hit.
UNKNOWN_PROFILE = BoardProfile(
    name="Unknown Pi (conservative fallback)",
    max_encoder_pixels=2_100_000,
)


_DEVICE_TREE_MODEL = "/proc/device-tree/model"


def _read_device_tree_model() -> str:
    """Read /proc/device-tree/model; strip the trailing NUL.

    Returns empty string if the file is missing or unreadable
    (test hosts, container CI, etc.).
    """
    try:
        with open(_DEVICE_TREE_MODEL, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace").rstrip("\x00").strip()


def get_board_profile(*, model_override: str | None = None) -> BoardProfile:
    """Identify the running board and return its profile.

    Args:
        model_override: Bypass the file read — for tests. If non-empty,
            substring-matches against BOARD_PROFILES the same way the
            real lookup does.

    Returns the matching profile, or ``UNKNOWN_PROFILE`` if no entry
    matches.
    """
    model = model_override or os.environ.get("HM_BOARD_MODEL", "")
    if not model:
        model = _read_device_tree_model()
    if not model:
        log.info("No device-tree model available; using unknown-board profile")
        return UNKNOWN_PROFILE
    needle = model.lower()
    for prefix, profile in BOARD_PROFILES.items():
        if prefix in needle:
            log.info("Detected board: %s (%s)", profile.name, model)
            return profile
    log.warning(
        "Board model %r not recognised; using unknown-board profile (cap=%d px)",
        model,
        UNKNOWN_PROFILE.max_encoder_pixels,
    )
    return UNKNOWN_PROFILE
