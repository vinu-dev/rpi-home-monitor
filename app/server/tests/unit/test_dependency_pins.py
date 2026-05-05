# REQ: SWR-071; RISK: RISK-027; SEC: SC-026; TEST: TC-056
"""Unit checks for security-sensitive dependency floors."""

from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]

FLASK_FLOOR = "3.1.3"
SECURITY_FILES = [
    (
        SERVER_ROOT / "requirements.txt",
        f"flask>={FLASK_FLOOR}",
        "flask>=3.0",
    ),
    (
        SERVER_ROOT / "setup.py",
        f'"flask>={FLASK_FLOOR}"',
        '"flask>=3.0"',
    ),
    (
        REPO_ROOT / "scripts" / "generate-sbom.sh",
        f'"name": "flask",\n      "version": ">={FLASK_FLOOR}"',
        '"name": "flask",\n      "version": ">=3.0"',
    ),
]


def _release_tuple(raw_version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw_version)
    assert match, f"Unparseable Flask version: {raw_version}"
    return tuple(int(part) for part in match.groups())


@pytest.mark.parametrize(("path", "expected", "legacy"), SECURITY_FILES)
def test_flask_floor_is_pinned_everywhere(
    path: Path, expected: str, legacy: str
) -> None:
    text = path.read_text(encoding="utf-8")
    assert expected in text
    assert legacy not in text


def test_installed_flask_version_meets_security_floor() -> None:
    assert _release_tuple(version("flask")) >= _release_tuple(FLASK_FLOOR)
