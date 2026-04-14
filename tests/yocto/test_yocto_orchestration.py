"""Repo-native Yocto automation checks used by CI orchestration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


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
