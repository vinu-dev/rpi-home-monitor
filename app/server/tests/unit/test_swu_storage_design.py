# REQ: SWR-010, SWR-038, SWR-046; RISK: RISK-004, RISK-018, RISK-019; SEC: SC-003; TEST: TC-013, TC-036, TC-044
"""Static guards for OTA storage and SWUpdate streaming design."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_swu_rootfs_images_stream_directly_to_inactive_slot():
    """Rootfs payloads must not be extracted into /tmp before flashing."""

    for template in (
        REPO_ROOT / "swupdate" / "sw-description.server",
        REPO_ROOT / "swupdate" / "sw-description.camera",
    ):
        text = template.read_text(encoding="utf-8")
        assert 'filename = "rootfs.ext4.gz";' in text
        assert 'type = "raw";' in text
        assert 'device = "/dev/monitor_standby";' in text
        assert "installed-directly = true;" in text

    build_script = (REPO_ROOT / "scripts" / "build-swu.sh").read_text(encoding="utf-8")
    assert "installed-directly" in build_script


def test_camera_ota_spool_is_data_backed():
    """Camera uploads must land on persistent /data, not rootfs or tmpfs."""

    expected = "/data/ota/camera-spool"
    files = (
        REPO_ROOT / "app" / "camera" / "camera_streamer" / "ota_installer.py",
        REPO_ROOT / "app" / "camera" / "scripts" / "camera-ota-installer.sh",
        REPO_ROOT / "app" / "camera" / "config" / "camera-ota-installer.path",
        REPO_ROOT / "app" / "camera" / "config" / "camera-ota-installer.service",
        REPO_ROOT / "app" / "camera" / "config" / "camera-ota-tmpfiles.conf",
    )
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert expected in text
        assert "/var/lib/camera-ota" not in text


def test_camera_installer_is_busybox_safe_and_uses_data_tmpdir():
    text = (
        REPO_ROOT / "app" / "camera" / "scripts" / "camera-ota-installer.sh"
    ).read_text(encoding="utf-8")

    assert "date -Iseconds" not in text
    assert "status=none" not in text
    assert 'export TMPDIR="$OTA_TMP"' in text
    assert "SPOOL=${CAMERA_OTA_SPOOL_DIR:-/data/ota/camera-spool}" in text
    assert 'OTA_TMP="$SPOOL/tmp"' in text


def test_partition_growth_has_findmnt_fallbacks():
    for script in (
        REPO_ROOT
        / "meta-home-monitor"
        / "recipes-core"
        / "first-boot"
        / "files"
        / "first-boot-setup.sh",
        REPO_ROOT
        / "meta-home-monitor"
        / "recipes-support"
        / "swupdate"
        / "files"
        / "swupdate-check.sh",
    ):
        text = script.read_text(encoding="utf-8")
        assert "mount_source()" in text
        assert "/proc/mounts" in text
        assert "findmnt -n -o SOURCE" in text
