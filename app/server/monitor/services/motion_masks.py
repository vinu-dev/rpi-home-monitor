"""Validation + normalisation helpers for per-camera motion masks."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

VALID_MASK_TYPES = {"motion_mask", "privacy_zone"}
VALID_REGION_SHAPES = {"rectangle", "polygon"}
VALID_REDACTION_TYPES = {"black_box", "blur"}

MAX_MASKS_PER_CAMERA = 20
MAX_REGIONS_PER_MASK = 8
MAX_POLYGON_POINTS = 50
MAX_MASK_NAME_LENGTH = 64
MAX_MASK_ID_LENGTH = 64


def normalize_motion_masks(raw_masks) -> list[dict]:
    """Return a validated, JSON-safe motion-mask list.

    Raises:
        ValueError: when the payload shape or values are invalid.
    """
    if not isinstance(raw_masks, list):
        raise ValueError("motion_masks must be a list")
    if len(raw_masks) > MAX_MASKS_PER_CAMERA:
        raise ValueError(
            f"motion_masks supports at most {MAX_MASKS_PER_CAMERA} entries"
        )

    normalized: list[dict] = []
    seen_ids: set[str] = set()
    for index, raw_mask in enumerate(raw_masks):
        path = f"motion_masks[{index}]"
        if not isinstance(raw_mask, dict):
            raise ValueError(f"{path} must be an object")

        mask_id = _normalize_mask_id(raw_mask.get("id"), path)
        if mask_id in seen_ids:
            raise ValueError(f"{path}.id duplicates another mask id: {mask_id}")
        seen_ids.add(mask_id)

        mask_type = raw_mask.get("type", "motion_mask")
        if mask_type not in VALID_MASK_TYPES:
            allowed = ", ".join(sorted(VALID_MASK_TYPES))
            raise ValueError(f"{path}.type must be one of: {allowed}")

        name = str(raw_mask.get("name", "") or "").strip()
        if not name or len(name) > MAX_MASK_NAME_LENGTH:
            raise ValueError(f"{path}.name must be 1-{MAX_MASK_NAME_LENGTH} characters")

        enabled = raw_mask.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"{path}.enabled must be a boolean")

        redaction_type = raw_mask.get("redaction_type")
        if mask_type == "privacy_zone":
            if redaction_type not in VALID_REDACTION_TYPES:
                allowed = ", ".join(sorted(VALID_REDACTION_TYPES))
                raise ValueError(f"{path}.redaction_type must be one of: {allowed}")
        elif redaction_type not in (None, ""):
            raise ValueError(
                f"{path}.redaction_type is only allowed for privacy_zone masks"
            )
        else:
            redaction_type = None

        regions = raw_mask.get("regions")
        if not isinstance(regions, list) or not regions:
            raise ValueError(f"{path}.regions must be a non-empty list")
        if len(regions) > MAX_REGIONS_PER_MASK:
            raise ValueError(
                f"{path}.regions supports at most {MAX_REGIONS_PER_MASK} regions"
            )

        normalized_regions = []
        for region_index, raw_region in enumerate(regions):
            normalized_regions.append(
                _normalize_region(raw_region, f"{path}.regions[{region_index}]")
            )

        normalized.append(
            {
                "id": mask_id,
                "type": mask_type,
                "name": name,
                "enabled": enabled,
                "redaction_type": redaction_type,
                "regions": normalized_regions,
            }
        )

    return normalized


def normalize_motion_mask(raw_mask: dict, *, default_id: str | None = None) -> dict:
    """Return a validated single mask object."""
    mask = deepcopy(raw_mask) if isinstance(raw_mask, dict) else raw_mask
    if isinstance(mask, dict) and default_id and not mask.get("id"):
        mask["id"] = default_id
    return normalize_motion_masks([mask])[0]


def create_motion_mask(existing_masks, raw_mask: dict) -> tuple[list[dict], dict]:
    """Append a validated mask to the existing list."""
    current = normalize_motion_masks(existing_masks or [])
    mask = normalize_motion_mask(raw_mask, default_id=f"mask-{uuid4().hex[:12]}")
    updated = [*current, mask]
    return normalize_motion_masks(updated), mask


def update_motion_mask(
    existing_masks, mask_id: str, raw_patch: dict
) -> tuple[list[dict], dict]:
    """Patch one existing mask and return the full updated list."""
    current = normalize_motion_masks(existing_masks or [])
    patch = deepcopy(raw_patch) if isinstance(raw_patch, dict) else raw_patch
    if not isinstance(patch, dict):
        raise ValueError("motion mask patch must be an object")

    for index, existing in enumerate(current):
        if existing["id"] != mask_id:
            continue
        merged = deepcopy(existing)
        merged.update(patch)
        merged["id"] = mask_id
        updated_mask = normalize_motion_mask(merged)
        current[index] = updated_mask
        return normalize_motion_masks(current), updated_mask
    raise KeyError(mask_id)


def delete_motion_mask(existing_masks, mask_id: str) -> tuple[list[dict], dict]:
    """Remove one existing mask from the list."""
    current = normalize_motion_masks(existing_masks or [])
    remaining = [mask for mask in current if mask["id"] != mask_id]
    if len(remaining) == len(current):
        raise KeyError(mask_id)
    deleted = next(mask for mask in current if mask["id"] == mask_id)
    return remaining, deleted


def _normalize_mask_id(raw_mask_id, path: str) -> str:
    mask_id = str(raw_mask_id or "").strip()
    if not mask_id or len(mask_id) > MAX_MASK_ID_LENGTH:
        raise ValueError(f"{path}.id must be 1-{MAX_MASK_ID_LENGTH} characters")
    return mask_id


def _normalize_region(raw_region, path: str) -> dict:
    if not isinstance(raw_region, dict):
        raise ValueError(f"{path} must be an object")

    shape = raw_region.get("shape")
    if shape not in VALID_REGION_SHAPES:
        allowed = ", ".join(sorted(VALID_REGION_SHAPES))
        raise ValueError(f"{path}.shape must be one of: {allowed}")

    coordinates = raw_region.get("coordinates")
    if not isinstance(coordinates, dict):
        raise ValueError(f"{path}.coordinates must be an object")

    if shape == "rectangle":
        rect = {
            "x": _normalize_percent(coordinates.get("x"), f"{path}.coordinates.x"),
            "y": _normalize_percent(coordinates.get("y"), f"{path}.coordinates.y"),
            "width": _normalize_percent(
                coordinates.get("width"),
                f"{path}.coordinates.width",
                allow_zero=False,
            ),
            "height": _normalize_percent(
                coordinates.get("height"),
                f"{path}.coordinates.height",
                allow_zero=False,
            ),
        }
        if rect["x"] + rect["width"] > 100 or rect["y"] + rect["height"] > 100:
            raise ValueError(
                f"{path}.coordinates rectangle must stay within 0-100 bounds"
            )
        return {
            "shape": "rectangle",
            "coordinates": rect,
        }

    points = coordinates.get("points")
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError(f"{path}.coordinates.points must contain at least 3 points")
    if len(points) > MAX_POLYGON_POINTS:
        raise ValueError(
            f"{path}.coordinates.points supports at most {MAX_POLYGON_POINTS} points"
        )

    normalized_points = []
    for point_index, raw_point in enumerate(points):
        if not isinstance(raw_point, dict):
            raise ValueError(
                f"{path}.coordinates.points[{point_index}] must be an object"
            )
        normalized_points.append(
            {
                "x": _normalize_percent(
                    raw_point.get("x"), f"{path}.coordinates.points[{point_index}].x"
                ),
                "y": _normalize_percent(
                    raw_point.get("y"), f"{path}.coordinates.points[{point_index}].y"
                ),
            }
        )

    return {
        "shape": "polygon",
        "coordinates": {
            "points": normalized_points,
        },
    }


def _normalize_percent(raw_value, path: str, *, allow_zero: bool = True) -> float:
    try:
        value = round(float(raw_value), 3)
    except (TypeError, ValueError):
        raise ValueError(f"{path} must be a number") from None
    if value < 0 or value > 100:
        raise ValueError(f"{path} must be between 0 and 100")
    if not allow_zero and value <= 0:
        raise ValueError(f"{path} must be greater than 0")
    return value
