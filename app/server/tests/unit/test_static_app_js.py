# REQ: SWR-010, SWR-032; RISK: RISK-004, RISK-017; SEC: SC-003, SC-016; TEST: TC-013, TC-028
"""Static checks for shared browser API error handling."""

import subprocess
import textwrap
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[2] / "monitor" / "static" / "js" / "app.js"
BASE_HTML = Path(__file__).resolve().parents[2] / "monitor" / "templates" / "base.html"
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


def test_api_retries_transient_get_failures_only():
    """Startup reload noise should not break read-only page boot calls."""
    text = APP_JS.read_text(encoding="utf-8")

    assert "var GET_RETRIES = 2;" in text
    assert "method === 'GET' ? GET_RETRIES : 0" in text
    assert "function _requestAttempt(method, url, body, retriesRemaining)" in text
    assert (
        "function _fetchWithNetworkRetry(method, url, opts, retriesRemaining)" in text
    )
    assert "function _isRetryableResponse(method, status)" in text
    assert "status === 502 || status === 503 || status === 504" in text
    assert "method === 'GET' &&" in text
    assert "method !== 'GET' && _csrfToken" in text


def test_api_get_network_retry_returns_response_once():
    """A retry success must not be parsed once, then treated as a Response."""
    script = textwrap.dedent(
        f"""
        const appPath = {str(APP_JS)!r};
        global.window = {{ location: {{ pathname: '/settings', href: '' }} }};
        global.document = {{
          readyState: 'loading',
          addEventListener: function() {{}},
          getElementById: function() {{ return null; }},
        }};
        global.setTimeout = function(fn) {{ fn(); return 1; }};
        let calls = 0;
        global.fetch = function() {{
          calls += 1;
          if (calls === 1) {{
            return Promise.reject(new Error('temporary network miss'));
          }}
          return Promise.resolve({{
            status: 200,
            ok: true,
            headers: {{ get: function() {{ return 'application/json'; }} }},
            json: function() {{ return Promise.resolve({{ ok: true }}); }},
            text: function() {{ return Promise.resolve(''); }},
          }});
        }};
        require(appPath);
        window.HM.api.get('/api/test').then(function(data) {{
          if (!data || data.ok !== true) {{
            throw new Error('unexpected data: ' + JSON.stringify(data));
          }}
          if (calls !== 2) {{
            throw new Error('expected one retry, got ' + calls + ' calls');
          }}
        }}).catch(function(err) {{
          console.error(err && err.stack ? err.stack : err);
          process.exit(1);
        }});
        """
    )

    subprocess.run(["node", "-e", script], check=True)


def test_api_reader_tolerates_already_parsed_retry_data():
    """Defensive guard: parsed retry data must not be read as a Response."""
    text = APP_JS.read_text(encoding="utf-8")

    assert "typeof resp.text !== 'function'" in text
    assert "typeof resp.json !== 'function'" in text
    assert "return Promise.resolve(resp || {});" in text


def test_app_js_asset_has_cache_buster():
    """Browsers must fetch new shared JS after OTA or dev deploy."""
    text = BASE_HTML.read_text(encoding="utf-8")

    assert "filename='js/app.js', v=" in text


def test_settings_boot_waits_for_shared_app_before_loading_panels():
    """Settings must not run admin loaders before auth/API are available."""
    text = SETTINGS_HTML.read_text(encoding="utf-8")

    assert "waitForSharedApp()" in text
    assert "Shared app runtime did not initialise" in text
    assert "Failed to initialise settings" in text
    assert "await this.waitForSharedApp();" in text
    assert "await this.loadSettings();" in text


def test_settings_boot_does_not_scan_unopened_tabs():
    """Opening System settings must not trigger unrelated panel APIs."""
    text = SETTINGS_HTML.read_text(encoding="utf-8")

    boot_start = text.index("async boot()")
    boot_end = text.index("/* --- Recording", boot_start)
    boot_block = text[boot_start:boot_end]

    for forbidden in [
        "this.loadWifi();",
        "this.loadStorageStatus();",
        "this.loadOffsiteBackup();",
        "this.scanUsb();",
        "this.loadNetworkInterfaces();",
        "this.loadTailscale();",
        "this.loadRecordingCameras();",
        "this.loadWebhooks();",
        "this.loadBackupSnapshots();",
        "this.loadOtaStatus();",
    ]:
        assert forbidden not in boot_block

    assert "this.runTabLoaders(this.tab);" in boot_block
    assert "if (tabId === 'storage')" in text
    assert "this.scanUsb();" in text


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


def test_common_camera_bundle_upload_visible_without_paired_cameras():
    """Admins can stage a reusable camera bundle before any camera is paired."""
    text = SETTINGS_HTML.read_text(encoding="utf-8")

    start = text.index('class="card ota-common-bundle"')
    common_bundle_opening = text[start : text.index(">", start) + 1]

    assert "x-show" not in common_bundle_opening
    assert "uploadCameraLibraryBundle" in text


def test_ota_install_buttons_lock_across_server_and_camera_updates():
    """Install starts are mutually exclusive; staging uploads remain separate."""
    text = SETTINGS_HTML.read_text(encoding="utf-8")

    for expected in [
        "serverInstallLockedByCamera",
        "cameraInstallLockedByServer",
        "A camera update is running. Server install is locked",
        "Server update is running. Camera installs are locked",
        "wait before installing a server update",
        "wait before starting camera updates",
        "serverInstallLockedByCamera() ||",
        "cameraAnyBusy() || cameraInstallLockedByServer()",
        "if (this.cameraInstallLockedByServer()) return false;",
    ]:
        assert expected in text
