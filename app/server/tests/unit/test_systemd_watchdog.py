# REQ: SWR-063; RISK: RISK-018; SEC: SC-019; TEST: TC-044, TC-047
"""Static checks for the monitor systemd watchdog directives."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
UNIT_FILE = REPO_ROOT / "app" / "server" / "config" / "monitor.service"

REQUIRED_DIRECTIVES = {
    "Type": "notify",
    "NotifyAccess": "main",
    "Restart": "always",
    "RestartSec": "5",
    "WatchdogSec": "60",
    "StartLimitIntervalSec": "300",
    "StartLimitBurst": "5",
}


def _read_directive(name: str) -> str:
    for raw_line in UNIT_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"Missing directive {name}= in {UNIT_FILE}")


@pytest.mark.parametrize("name,expected", sorted(REQUIRED_DIRECTIVES.items()))
def test_monitor_service_watchdog_directives(name: str, expected: str):
    assert _read_directive(name) == expected
