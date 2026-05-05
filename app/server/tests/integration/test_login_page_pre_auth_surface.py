# REQ: SWR-022, SWR-099; RISK: RISK-010, RISK-099; SEC: SC-010, SC-099; TEST: TC-021, TC-099
"""Regression tests for the login page's pre-auth recovery copy."""

from __future__ import annotations

import re
from pathlib import Path


def test_login_page_says_only_contact_your_administrator(app, client):
    setup_stamp = Path(app.config["DATA_DIR"]) / ".setup-done"
    setup_stamp.write_text("done", encoding="utf-8")

    response = client.get("/login")
    assert response.status_code == 200

    body = response.get_data(as_text=True)
    assert body.count("Contact your administrator if you can't sign in.") == 1

    match = re.search(
        r'<p class="forgot-password--minimal">\s*(.*?)\s*</p>',
        body,
        re.DOTALL,
    )
    assert match is not None
    hint = match.group(1)
    hint_lowered = hint.lower()
    assert "<a" not in hint.lower()

    lowered = body.lower()
    for forbidden in ("sudo", "reset-admin", "ssh", "factory reset", "/opt/monitor"):
        assert forbidden not in lowered

    for forbidden in (
        "recovery code",
        "reset code",
    ):
        assert forbidden not in hint_lowered
