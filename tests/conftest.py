"""Shared collection rules for top-level automated validation suites."""

from pathlib import Path

import pytest

LAYER_MARKERS = {
    "hardware": "hardware",
    "soak": "soak",
}


def pytest_collection_modifyitems(config, items):
    """Assign and enforce markers for top-level test suites."""
    for item in items:
        path = Path(str(item.fspath))
        parts = path.parts
        marker = None
        if "hardware" in parts:
            marker = "hardware"
        elif "soak" in parts:
            marker = "soak"
        elif "playwright" in parts:
            marker = "e2e"
        elif "yocto" in parts:
            marker = "integration"

        if marker is None:
            continue

        marker_names = {mark.name for mark in item.iter_markers()}
        if marker not in marker_names:
            item.add_marker(getattr(pytest.mark, marker))
