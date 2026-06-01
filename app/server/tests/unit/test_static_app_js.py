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
        "ota-common-bundle",
        "camera_bundle",
        "uploadCameraLibraryBundle",
        "openCommonCameraBundlePicker",
        'id="camera-common-bundle-file"',
        "ota-file-picker__input",
        "uploadCustomCameraBundle",
        "pushAllCameraBundles",
        "ota-device-card",
        "ota-job-panel",
        "otaProgressValue",
        "otaStateLabel",
        "otaStateTone",
        "cameraOtaMessage",
        "Camera is rebooting into the new slot.",
    ]:
        assert expected in text


def test_common_camera_bundle_picker_cannot_be_left_disabled():
    """The common picker is a launch button, not a disabled file input label."""
    text = SETTINGS_HTML.read_text(encoding="utf-8")

    start = text.index('@click="openCommonCameraBundlePicker()"')
    end = text.index(
        '@change="uploadCameraLibraryBundle($event.target.files[0]); '
        "$event.target.value = '';",
        start,
    )
    common_picker_block = text[start - 300 : end + 120]

    assert 'type="button"' in common_picker_block
    assert '@click="openCommonCameraBundlePicker()"' in common_picker_block
    assert 'style="position:absolute;left:-10000px;' in common_picker_block
    assert ":disabled=" not in common_picker_block
    assert "input.disabled = false;" in text


def test_ota_install_buttons_lock_across_server_and_camera_updates():
    """Install starts are mutually exclusive; staging uploads remain separate."""
    text = SETTINGS_HTML.read_text(encoding="utf-8")

    for expected in [
        "serverInstallLockedByCamera",
        "cameraInstallLockedByServer",
        "this.ota.operation && this.ota.operation.kind === 'camera-update'",
        "this.ota.operation && this.ota.operation.kind === 'server-install'",
        "this.ota.server._clientStartingInstall = true;",
        "A camera update is running. Server install is locked",
        "Server update is running. Camera installs are locked",
        "wait before installing a server update",
        "wait before starting camera updates",
        "serverInstallLockedByCamera() ||",
        "cameraAnyBusy() || cameraInstallLockedByServer()",
        "if (this.cameraInstallLockedByServer()) return false;",
    ]:
        assert expected in text
    assert "this.ota.server.state = 'installing';" not in text
