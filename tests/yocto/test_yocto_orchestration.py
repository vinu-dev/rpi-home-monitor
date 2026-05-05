"""Repo-native Yocto automation checks used by CI orchestration."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FLASK_FLOOR = (3, 1, 3)


@pytest.mark.integration
def test_parse_prerequisites_are_present():
    assert (
        REPO_ROOT / "meta-home-monitor" / "conf" / "distro" / "home-monitor.conf"
    ).is_file()
    assert (REPO_ROOT / "config" / "bblayers.conf").is_file()


@pytest.mark.integration
@pytest.mark.slow
def test_bitbake_parse_if_available():
    if shutil.which("bitbake") is None:
        pytest.skip("bitbake not installed in this environment")
    subprocess.run(["bitbake", "-p"], check=True, cwd=REPO_ROOT)


def _parse_release(raw_version: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", raw_version)
    if not match:
        raise AssertionError(f"Unparseable Yocto package version: {raw_version}")
    return tuple(int(part) for part in match.groups())


@pytest.mark.integration
def test_flask_version_floor_in_yocto_manifests():
    deploy_dir = os.environ.get("YOCTO_DEPLOY_DIR")
    if not deploy_dir:
        pytest.skip("YOCTO_DEPLOY_DIR not set")

    manifests = sorted(Path(deploy_dir).glob("*.manifest"))
    if not manifests:
        pytest.skip(f"No Yocto manifests found in {deploy_dir}")

    found_flask = False
    for manifest in manifests:
        for line in manifest.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 3 or parts[0] != "python3-flask":
                continue
            found_flask = True
            assert _parse_release(parts[2]) >= FLASK_FLOOR, (
                f"{manifest.name} resolves python3-flask to {parts[2]}, "
                "expected >= 3.1.3"
            )

    assert found_flask, "Expected at least one python3-flask entry in Yocto manifests"
