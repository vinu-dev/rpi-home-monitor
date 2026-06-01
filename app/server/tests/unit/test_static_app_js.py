# REQ: SWR-010, SWR-032; RISK: RISK-004, RISK-017; SEC: SC-003, SC-016; TEST: TC-013, TC-028
"""Static checks for shared browser API error handling."""

from pathlib import Path

APP_JS = Path(__file__).resolve().parents[2] / "monitor" / "static" / "js" / "app.js"
SETTINGS_HTML = (
    Path(__file__).resolve().parents[2] / "monitor" / "templates" / "settings.html"
)


def test_api_error_reader_hides_html_startup_bodies():
    """Raw nginx startup HTML must not be shown inside UI status fields."""
    text = APP_JS.read_text(encoding="utf-8")

    assert "_messageFromNonJsonResponse" in text
    assert "looksLikeHtml" in text
    assert "Server is restarting; try again in a moment." in text
    assert "Server returned an HTML page instead of API data." in text


def test_server_ota_install_treats_restart_fallback_as_rebooting():
    """The OTA card should keep polling when nginx reports startup JSON."""
    text = SETTINGS_HTML.read_text(encoding="utf-8")

    assert "_serverRestartingError" in text
    assert "home monitor is restarting" in text
    assert "state = 'rebooting'" in text


def test_camera_ota_uses_dedicated_multi_camera_status_layout():
    """Camera OTA cards need stable state copy and controls per device."""
    text = SETTINGS_HTML.read_text(encoding="utf-8")

    for expected in [
        "ota-camera-list",
        "ota-device-card",
        "ota-job-panel",
        "otaProgressValue",
        "otaStateLabel",
        "otaStateTone",
        "cameraOtaMessage",
        "Camera is rebooting into the new slot.",
    ]:
        assert expected in text
