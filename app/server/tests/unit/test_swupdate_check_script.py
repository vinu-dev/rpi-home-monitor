# REQ: SWR-010, SWR-046; RISK: RISK-004, RISK-019; SEC: SC-003, SC-018; TEST: TC-013, TC-043
"""Static checks for the SWUpdate post-boot rollback helper."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = (
    REPO_ROOT
    / "meta-home-monitor"
    / "recipes-support"
    / "swupdate"
    / "files"
    / "swupdate-check.sh"
)
BBAPPEND = (
    REPO_ROOT
    / "meta-home-monitor"
    / "recipes-support"
    / "swupdate"
    / "swupdate_%.bbappend"
)


def test_failed_pending_ota_requests_retry_reboot():
    """A failed updated slot must keep moving toward U-Boot rollback."""
    text = SCRIPT.read_text(encoding="utf-8")

    assert "request_retry_reboot()" in text
    assert "systemctl --no-block reboot" in text
    assert "Health checks FAILED" in text
    assert re.search(r"Health checks FAILED[\s\S]+request_retry_reboot", text)


def test_systemd_timeout_exceeds_script_deadline():
    """systemd must not kill the script before its own rollback path runs."""
    script = SCRIPT.read_text(encoding="utf-8")
    unit = BBAPPEND.read_text(encoding="utf-8")

    deadline = int(re.search(r"\+\s*(\d+)\s*\)\)", script).group(1))
    timeout = int(re.search(r"TimeoutStartSec=(\d+)", unit).group(1))
    assert timeout > deadline
