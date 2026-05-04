"""Unit tests for camera-side motion-mask rasterisation helpers."""

from __future__ import annotations

import numpy as np

from camera_streamer.motion_masks import (
    apply_motion_mask,
    build_motion_exclusion_mask,
    validate_motion_masks,
)


def test_validate_motion_masks_rejects_out_of_bounds_rectangle():
    error = validate_motion_masks(
        [
            {
                "id": "mask-1",
                "type": "motion_mask",
                "name": "Bad rect",
                "enabled": True,
                "redaction_type": None,
                "regions": [
                    {
                        "shape": "rectangle",
                        "coordinates": {"x": 80, "y": 10, "width": 30, "height": 20},
                    }
                ],
            }
        ]
    )
    assert "rectangle must stay within 0-100 bounds" in error


def test_build_motion_exclusion_mask_handles_polygon():
    mask = build_motion_exclusion_mask(
        [
            {
                "id": "poly-1",
                "type": "motion_mask",
                "name": "Triangle",
                "enabled": True,
                "redaction_type": None,
                "regions": [
                    {
                        "shape": "polygon",
                        "coordinates": {
                            "points": [
                                {"x": 10, "y": 10},
                                {"x": 90, "y": 10},
                                {"x": 50, "y": 90},
                            ]
                        },
                    }
                ],
            }
        ],
        (100, 100),
    )
    assert mask is not None
    assert bool(mask[50, 50]) is True
    assert bool(mask[5, 5]) is False


def test_apply_motion_mask_zeroes_masked_pixels():
    frame = np.full((10, 10), 255, dtype=np.uint8)
    exclusion = np.zeros((10, 10), dtype=bool)
    exclusion[2:5, 2:5] = True
    masked = apply_motion_mask(frame, exclusion)
    assert int(masked[3, 3]) == 0
    assert int(masked[0, 0]) == 255
