"""Camera-side motion-mask validation and rasterisation helpers."""

from __future__ import annotations

import numpy as np

VALID_MASK_TYPES = {"motion_mask", "privacy_zone"}
VALID_REGION_SHAPES = {"rectangle", "polygon"}
VALID_REDACTION_TYPES = {"black_box", "blur"}
MAX_MASKS_PER_CAMERA = 20
MAX_REGIONS_PER_MASK = 8
MAX_POLYGON_POINTS = 50


def validate_motion_masks(raw_masks) -> str:
    """Return an error string when the payload is invalid, else ``""``."""
    if not isinstance(raw_masks, list):
        return "motion_masks: expected list"
    if len(raw_masks) > MAX_MASKS_PER_CAMERA:
        return f"motion_masks: maximum is {MAX_MASKS_PER_CAMERA}"

    seen_ids: set[str] = set()
    for index, raw_mask in enumerate(raw_masks):
        path = f"motion_masks[{index}]"
        if not isinstance(raw_mask, dict):
            return f"{path}: expected object"

        mask_id = str(raw_mask.get("id", "") or "").strip()
        if not mask_id:
            return f"{path}.id: required"
        if mask_id in seen_ids:
            return f"{path}.id: duplicate {mask_id}"
        seen_ids.add(mask_id)

        mask_type = raw_mask.get("type", "motion_mask")
        if mask_type not in VALID_MASK_TYPES:
            return f"{path}.type: must be one of {sorted(VALID_MASK_TYPES)}"

        name = str(raw_mask.get("name", "") or "").strip()
        if not name or len(name) > 64:
            return f"{path}.name: must be 1-64 characters"

        enabled = raw_mask.get("enabled", True)
        if not isinstance(enabled, bool):
            return f"{path}.enabled: expected bool"

        redaction_type = raw_mask.get("redaction_type")
        if mask_type == "privacy_zone":
            if redaction_type not in VALID_REDACTION_TYPES:
                return (
                    f"{path}.redaction_type: must be one of "
                    f"{sorted(VALID_REDACTION_TYPES)}"
                )
        elif redaction_type not in (None, ""):
            return f"{path}.redaction_type: only valid for privacy_zone masks"

        regions = raw_mask.get("regions")
        if not isinstance(regions, list) or not regions:
            return f"{path}.regions: expected non-empty list"
        if len(regions) > MAX_REGIONS_PER_MASK:
            return f"{path}.regions: maximum is {MAX_REGIONS_PER_MASK}"

        for region_index, raw_region in enumerate(regions):
            error = _validate_region(raw_region, f"{path}.regions[{region_index}]")
            if error:
                return error

    return ""


def build_motion_exclusion_mask(
    motion_masks, frame_shape: tuple[int, int] | tuple[int, int, int]
) -> np.ndarray | None:
    """Return a boolean exclusion mask for enabled motion/privacy regions."""
    error = validate_motion_masks(motion_masks or [])
    if error:
        raise ValueError(error)

    height, width = int(frame_shape[0]), int(frame_shape[1])
    bitmap = np.zeros((height, width), dtype=bool)

    for mask in motion_masks or []:
        if not mask.get("enabled", True):
            continue
        for region in mask.get("regions", []):
            if region.get("shape") == "rectangle":
                _paint_rectangle(bitmap, region["coordinates"])
            elif region.get("shape") == "polygon":
                _paint_polygon(bitmap, region["coordinates"].get("points", []))

    return bitmap if bitmap.any() else None


def apply_motion_mask(
    frame: np.ndarray, exclusion_mask: np.ndarray | None
) -> np.ndarray:
    """Return a frame with masked pixels zeroed out."""
    if exclusion_mask is None:
        return frame
    masked = frame.copy()
    if masked.ndim == 2:
        masked[exclusion_mask] = 0
        return masked
    if masked.ndim == 3:
        masked[exclusion_mask, :] = 0
        return masked
    raise ValueError(f"apply_motion_mask expects 2-D or 3-D frame, got {masked.ndim}")


def _validate_region(raw_region, path: str) -> str:
    if not isinstance(raw_region, dict):
        return f"{path}: expected object"
    shape = raw_region.get("shape")
    if shape not in VALID_REGION_SHAPES:
        return f"{path}.shape: must be one of {sorted(VALID_REGION_SHAPES)}"
    coordinates = raw_region.get("coordinates")
    if not isinstance(coordinates, dict):
        return f"{path}.coordinates: expected object"

    if shape == "rectangle":
        rect = {}
        for key in ("x", "y", "width", "height"):
            value = coordinates.get(key)
            try:
                rect[key] = float(value)
            except (TypeError, ValueError):
                return f"{path}.coordinates.{key}: expected number"
        if not (0 <= rect["x"] <= 100 and 0 <= rect["y"] <= 100):
            return f"{path}.coordinates: x/y must be between 0 and 100"
        if rect["width"] <= 0 or rect["height"] <= 0:
            return f"{path}.coordinates: width/height must be greater than 0"
        if rect["x"] + rect["width"] > 100 or rect["y"] + rect["height"] > 100:
            return f"{path}.coordinates: rectangle must stay within 0-100 bounds"
        return ""

    points = coordinates.get("points")
    if not isinstance(points, list) or len(points) < 3:
        return f"{path}.coordinates.points: expected at least 3 points"
    if len(points) > MAX_POLYGON_POINTS:
        return f"{path}.coordinates.points: maximum is {MAX_POLYGON_POINTS}"
    for point_index, point in enumerate(points):
        if not isinstance(point, dict):
            return f"{path}.coordinates.points[{point_index}]: expected object"
        for key in ("x", "y"):
            value = point.get(key)
            try:
                coord = float(value)
            except (TypeError, ValueError):
                return (
                    f"{path}.coordinates.points[{point_index}].{key}: expected number"
                )
            if coord < 0 or coord > 100:
                return (
                    f"{path}.coordinates.points[{point_index}].{key}: "
                    "must be between 0 and 100"
                )
    return ""


def _paint_rectangle(bitmap: np.ndarray, coordinates: dict) -> None:
    height, width = bitmap.shape
    x0 = _scale_x(float(coordinates["x"]), width)
    y0 = _scale_y(float(coordinates["y"]), height)
    x1 = _scale_x(float(coordinates["x"]) + float(coordinates["width"]), width)
    y1 = _scale_y(float(coordinates["y"]) + float(coordinates["height"]), height)
    if x1 <= x0 or y1 <= y0:
        return
    bitmap[y0:y1, x0:x1] = True


def _paint_polygon(bitmap: np.ndarray, raw_points: list[dict]) -> None:
    if len(raw_points) < 3:
        return
    height, width = bitmap.shape
    points = np.array(
        [
            (_scale_x(float(point["x"]), width), _scale_y(float(point["y"]), height))
            for point in raw_points
        ],
        dtype=np.float32,
    )
    min_x = max(0, int(np.floor(points[:, 0].min())))
    max_x = min(width - 1, int(np.ceil(points[:, 0].max())))
    min_y = max(0, int(np.floor(points[:, 1].min())))
    max_y = min(height - 1, int(np.ceil(points[:, 1].max())))
    if min_x > max_x or min_y > max_y:
        return

    grid_x, grid_y = np.meshgrid(
        np.arange(min_x, max_x + 1, dtype=np.float32) + 0.5,
        np.arange(min_y, max_y + 1, dtype=np.float32) + 0.5,
    )
    inside = np.zeros_like(grid_x, dtype=bool)
    x1 = points[:, 0]
    y1 = points[:, 1]
    x2 = np.roll(x1, -1)
    y2 = np.roll(y1, -1)
    for start_x, start_y, end_x, end_y in zip(x1, y1, x2, y2, strict=False):
        intersects = ((start_y > grid_y) != (end_y > grid_y)) & (
            grid_x
            < (end_x - start_x) * (grid_y - start_y) / ((end_y - start_y) or 1e-6)
            + start_x
        )
        inside ^= intersects
    bitmap[min_y : max_y + 1, min_x : max_x + 1] |= inside


def _scale_x(percent: float, width: int) -> int:
    return max(0, min(width, round((percent / 100.0) * width)))


def _scale_y(percent: float, height: int) -> int:
    return max(0, min(height, round((percent / 100.0) * height)))
