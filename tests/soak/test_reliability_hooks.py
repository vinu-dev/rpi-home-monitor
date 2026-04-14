"""Soak and endurance entrypoints."""

from __future__ import annotations

import os
import time

import pytest


@pytest.mark.soak
def test_soak_environment_is_declared():
    if not os.environ.get("SOAK_ENABLED"):
        pytest.skip("set SOAK_ENABLED=1 to run soak scenarios")
    time.sleep(1)
    assert True
