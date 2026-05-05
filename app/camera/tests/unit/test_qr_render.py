# REQ: SWR-036; RISK: RISK-012; SEC: SC-012; TEST: TC-034
"""Node-backed tests for setup-page QR rendering behavior."""

import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("qr_harness.js")


@pytest.mark.parametrize(
    "scenario",
    [
        "setup-complete",
        "connected-poll",
        "idempotent-replace",
        "broken-library",
    ],
)
def test_setup_qr_harness_scenarios(scenario):
    result = subprocess.run(
        ["node", str(HARNESS), scenario],
        capture_output=True,
        text=True,
        check=True,
    )
    assert f'"scenario":"{scenario}"' in result.stdout
