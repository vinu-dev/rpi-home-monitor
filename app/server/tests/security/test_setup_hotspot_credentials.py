# REQ: SWR-021, SWR-036; RISK: RISK-010; SEC: SC-010; TEST: TC-021, TC-044
"""Regression tests for first-boot hotspot credential rotation."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SERVER_SCRIPT = REPO_ROOT / "app/server/config/monitor-hotspot.sh"
CAMERA_SCRIPT = REPO_ROOT / "app/camera/config/camera-hotspot.sh"
GPIO_TRIGGER_SCRIPT = REPO_ROOT / "app/server/config/gpio-trigger.sh"
GPIO_TRIGGER_RECIPE = (
    REPO_ROOT / "meta-home-monitor/recipes-core/gpio-trigger/gpio-trigger_1.0.bb"
)
BASE_PACKAGEGROUP = (
    REPO_ROOT
    / "meta-home-monitor/recipes-core/packagegroups/packagegroup-monitor-base.bb"
)
DEPLOY_SCRIPT = REPO_ROOT / "scripts/deploy-dev-app.sh"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_hotspot_scripts_allow_documented_first_boot_defaults_only():
    for path, default_assignment in (
        (SERVER_SCRIPT, 'HOTSPOT_DEFAULT_PASS="homemonitor"'),
        (CAMERA_SCRIPT, 'HOTSPOT_DEFAULT_PASS="homecamera"'),
    ):
        text = _read(path)
        assert default_assignment in text
        assert 'SOURCE="default"' in text
        assert 'wifi-sec.psk "${HOTSPOT_PASS}"' not in text


def test_rotated_hotspot_credentials_must_not_reuse_factory_defaults():
    for path in (SERVER_SCRIPT, CAMERA_SCRIPT):
        text = _read(path)
        assert "HOTSPOT_PASS_FILE=" in text
        assert "get_hotspot_pass()" in text
        assert "known public setup hotspot credential is not allowed" in text
        assert "setup hotspot credential must be at least 12 characters" in text
        assert 'wifi-sec.psk "${HOTSPOT_PASS_VALUE}"' in text


def test_setup_hotspots_advertise_wpa2_ccmp_not_legacy_wpa():
    for path in (SERVER_SCRIPT, CAMERA_SCRIPT):
        text = _read(path)
        assert "wifi-sec.key-mgmt wpa-psk" in text
        assert "wifi-sec.proto rsn" in text
        assert "wifi-sec.pairwise ccmp" in text
        assert "wifi-sec.group ccmp" in text


def test_dev_deploy_installs_setup_hotspot_scripts():
    text = _read(DEPLOY_SCRIPT)

    assert "app/server/config/monitor-hotspot.sh" in text
    assert "app/server/config/monitor-hotspot.service" in text
    assert "/opt/monitor/scripts/monitor-hotspot.sh" in text
    assert "monitor-hotspot.service" in text

    assert "app/camera/config/camera-hotspot.sh" in text
    assert "app/camera/config/camera-hotspot.service" in text
    assert "/opt/camera/scripts/camera-hotspot.sh" in text
    assert "camera-hotspot.service" in text


def test_dev_deploy_installs_camera_privileged_helper():
    text = _read(DEPLOY_SCRIPT)

    assert "app/camera/config/camera-privileged-helper.service" in text
    assert "camera-privileged-helper.service" in text
    assert "systemctl restart camera-privileged-helper camera-streamer" in text


def test_hotspot_wipe_tolerates_read_only_rootfs_cleanup():
    for path in (SERVER_SCRIPT, CAMERA_SCRIPT):
        text = _read(path)
        assert "Skipped read-only file" in text
        assert "Skipped read-only wpa_supplicant.conf" in text


def test_hotspot_led_writes_are_best_effort_under_set_e():
    for path in (SERVER_SCRIPT, CAMERA_SCRIPT):
        text = _read(path)
        assert 'TARGET="${LED_PATH}/$1"' in text
        assert 'if [ -w "$TARGET" ]; then' in text
        assert 'printf "%s\\n" "$2" > "$TARGET" 2>/dev/null || true' in text


def test_wifi_repair_wipe_keeps_provisioned_setup_credential():
    for path in (SERVER_SCRIPT, CAMERA_SCRIPT):
        text = _read(path)
        assert 'rm -f "$HOTSPOT_PASS_FILE"' not in text


def test_hardware_factory_reset_removes_runtime_setup_state():
    text = _read(GPIO_TRIGGER_SCRIPT)
    assert 'find "$CONFIG_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +' in text
    assert 'FIRST_BOOT_STAMP="/data/.first-boot-done"' in text
    assert "backup-snapshots network" in text
    assert "/etc/home-monitor/trust" in text


def test_authenticated_server_factory_reset_wipes_setup_hotspot_credential():
    server_reset = _read(REPO_ROOT / "app/server/monitor/services/backup_paths.py")
    camera_reset = _read(REPO_ROOT / "app/camera/camera_streamer/factory_reset.py")

    assert (
        "setup_hotspot_password_file"
        in server_reset.partition("def resettable_config_files")[2].partition(
            "def resettable_dirs"
        )[0]
    )
    assert "camera-hotspot.psk" not in camera_reset
    assert "operator-chosen setup hotspot password" in camera_reset


def test_gpio_trigger_is_installed_in_shared_images():
    recipe = _read(GPIO_TRIGGER_RECIPE)
    packagegroup = _read(BASE_PACKAGEGROUP)

    assert "file://config/gpio-trigger.sh" in recipe
    assert "file://config/gpio-trigger.service" in recipe
    assert "/opt/scripts/gpio-trigger.sh" in recipe
    assert "gpio-trigger.service" in recipe
    assert "gpio-trigger" in packagegroup
