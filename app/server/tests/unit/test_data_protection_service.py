# REQ: SWR-101-A; RISK: RISK-101-1; SEC: SC-005, SC-101; TEST: TC-101-AC-12
"""Tests for runtime /data encryption posture detection."""

from monitor.services.data_protection import DataProtectionService


def _write_mountinfo(path, *, major_minor, mount_point="/data", source="/dev/mmcblk0p4"):
    path.write_text(
        f"42 1 {major_minor} / {mount_point} rw,relatime - ext4 {source} rw\n"
    )


def test_detects_luks_dmcrypt_data_volume(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    sysroot = tmp_path / "sys"
    _write_mountinfo(
        mountinfo,
        major_minor="253:0",
        source="/dev/mapper/data",
    )
    svc = DataProtectionService(
        data_dir="/data",
        proc_mountinfo_path=str(mountinfo),
        sys_dev_block_root=str(sysroot),
    )
    svc._read_dm_uuid = lambda _major_minor: "CRYPT-LUKS2-test"

    status = svc.status()

    assert status["state"] == "encrypted"
    assert status["protected"] is True
    assert status["secret_enrollment_blocked"] is False


def test_detects_explicitly_unencrypted_data_volume(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    _write_mountinfo(mountinfo, major_minor="179:4")

    status = DataProtectionService(
        data_dir="/data",
        proc_mountinfo_path=str(mountinfo),
        sys_dev_block_root=str(tmp_path / "sys"),
    ).status()

    assert status["state"] == "unencrypted"
    assert status["protected"] is False
    assert "plaintext" in status["warning"]


def test_unknown_when_mountinfo_unavailable(tmp_path):
    status = DataProtectionService(
        data_dir="/data",
        proc_mountinfo_path=str(tmp_path / "missing"),
        sys_dev_block_root=str(tmp_path / "sys"),
    ).status()

    assert status["state"] == "unknown"
    assert status["protected"] is False
    assert status["requires_attention"] is True


def test_security_profile_blocks_secret_writes_when_not_encrypted(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    _write_mountinfo(mountinfo, major_minor="179:4")
    svc = DataProtectionService(
        data_dir="/data",
        require_encrypted=True,
        proc_mountinfo_path=str(mountinfo),
        sys_dev_block_root=str(tmp_path / "sys"),
    )

    allowed, payload = svc.check_secret_write_allowed("totp_enrollment")

    assert allowed is False
    assert payload["error"] == "data_encryption_required"
    assert payload["feature"] == "totp_enrollment"
    assert payload["data_protection"]["secret_enrollment_blocked"] is True


def test_marker_file_enforces_encryption_policy(tmp_path):
    mountinfo = tmp_path / "mountinfo"
    marker = tmp_path / "require-encrypted-data"
    _write_mountinfo(mountinfo, major_minor="179:4")
    marker.write_text("1")
    svc = DataProtectionService(
        data_dir="/data",
        require_marker_path=str(marker),
        proc_mountinfo_path=str(mountinfo),
        sys_dev_block_root=str(tmp_path / "sys"),
    )

    status = svc.status()

    assert status["enforcement_required"] is True
    assert status["secret_enrollment_blocked"] is True
