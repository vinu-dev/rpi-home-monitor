"""Tests for monitor.services.usb — USB storage detection and management."""

import json
import subprocess
from unittest.mock import MagicMock, patch

from monitor.services.usb import (
    DEFAULT_MOUNT_POINT,
    RECORDINGS_FOLDER,
    SUPPORTED_FS,
    _device_info,
    _human_size,
    detect_devices,
    format_device,
    is_mounted,
    mount_device,
    prepare_recordings_dir,
    unmount_device,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _lsblk_output(blockdevices):
    """Build a fake lsblk JSON stdout string."""
    return json.dumps({"blockdevices": blockdevices})


FAKE_USB_DISK = {
    "name": "sda",
    "path": "/dev/sda",
    "size": 32000000000,
    "fstype": None,
    "mountpoint": None,
    "model": "SanDisk Ultra  ",
    "label": None,
    "tran": "usb",
    "type": "disk",
    "children": [
        {
            "name": "sda1",
            "path": "/dev/sda1",
            "size": 31999000000,
            "fstype": "ext4",
            "mountpoint": None,
            "model": None,
            "label": "MYUSB",
            "tran": None,
            "type": "part",
        }
    ],
}

FAKE_USB_NO_PARTITIONS = {
    "name": "sdb",
    "path": "/dev/sdb",
    "size": 8000000000,
    "fstype": "vfat",
    "mountpoint": None,
    "model": "Generic Flash",
    "label": "FLASH",
    "tran": "usb",
    "type": "disk",
    "children": [],
}

FAKE_NON_USB_DISK = {
    "name": "mmcblk0",
    "path": "/dev/mmcblk0",
    "size": 64000000000,
    "fstype": None,
    "mountpoint": None,
    "model": None,
    "label": None,
    "tran": None,
    "type": "disk",
    "children": [],
}


def _make_run_result(returncode=0, stdout="", stderr=""):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ===========================================================================
# detect_devices
# ===========================================================================


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_with_usb_partitions(mock_run):
    """USB disk with partitions returns one entry per partition."""
    mock_run.return_value = _make_run_result(
        stdout=_lsblk_output([FAKE_USB_DISK, FAKE_NON_USB_DISK])
    )
    devices = detect_devices()

    assert len(devices) == 1
    dev = devices[0]
    assert dev["name"] == "sda1"
    assert dev["path"] == "/dev/sda1"
    assert dev["fstype"] == "ext4"
    assert dev["supported"] is True
    assert dev["model"] == "SanDisk Ultra"
    assert dev["label"] == "MYUSB"
    assert dev["size_bytes"] == 31999000000


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_whole_disk_no_partitions(mock_run):
    """USB disk with no children returns the whole device."""
    mock_run.return_value = _make_run_result(
        stdout=_lsblk_output([FAKE_USB_NO_PARTITIONS])
    )
    devices = detect_devices()

    assert len(devices) == 1
    assert devices[0]["name"] == "sdb"
    assert devices[0]["fstype"] == "vfat"
    assert devices[0]["supported"] is True


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_no_usb(mock_run):
    """No USB devices returns empty list."""
    mock_run.return_value = _make_run_result(stdout=_lsblk_output([FAKE_NON_USB_DISK]))
    assert detect_devices() == []


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_empty_blockdevices(mock_run):
    """Empty blockdevices list returns empty."""
    mock_run.return_value = _make_run_result(stdout=_lsblk_output([]))
    assert detect_devices() == []


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_lsblk_failure(mock_run):
    """Non-zero return code from lsblk returns empty list."""
    mock_run.return_value = _make_run_result(returncode=1, stderr="lsblk: not found")
    assert detect_devices() == []


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_lsblk_timeout(mock_run):
    """Timeout from lsblk returns empty list."""
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="lsblk", timeout=10)
    assert detect_devices() == []


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_invalid_json(mock_run):
    """Malformed JSON from lsblk returns empty list."""
    mock_run.return_value = _make_run_result(stdout="not valid json{{{")
    assert detect_devices() == []


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_os_error(mock_run):
    """OSError (e.g. lsblk binary missing) returns empty list."""
    mock_run.side_effect = OSError("No such file or directory")
    assert detect_devices() == []


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_multiple_usb(mock_run):
    """Multiple USB disks with partitions are all returned."""
    second_usb = {
        "name": "sdc",
        "path": "/dev/sdc",
        "size": 16000000000,
        "fstype": None,
        "mountpoint": None,
        "model": "Kingston  ",
        "label": None,
        "tran": "usb",
        "type": "disk",
        "children": [
            {
                "name": "sdc1",
                "path": "/dev/sdc1",
                "size": 8000000000,
                "fstype": "ntfs",
                "mountpoint": None,
                "model": None,
                "label": "DATA",
                "tran": None,
                "type": "part",
            },
            {
                "name": "sdc2",
                "path": "/dev/sdc2",
                "size": 8000000000,
                "fstype": "exfat",
                "mountpoint": "/mnt/usb",
                "model": None,
                "label": "BACKUP",
                "tran": None,
                "type": "part",
            },
        ],
    }
    mock_run.return_value = _make_run_result(
        stdout=_lsblk_output([FAKE_USB_DISK, second_usb])
    )
    devices = detect_devices()
    assert len(devices) == 3
    names = [d["name"] for d in devices]
    assert "sda1" in names
    assert "sdc1" in names
    assert "sdc2" in names


@patch("monitor.services.usb.subprocess.run")
def test_detect_devices_unsupported_fs(mock_run):
    """Partition with unsupported fstype has supported=False."""
    usb = {
        "name": "sda",
        "path": "/dev/sda",
        "size": 4000000000,
        "fstype": None,
        "mountpoint": None,
        "model": "Drive",
        "label": None,
        "tran": "usb",
        "type": "disk",
        "children": [
            {
                "name": "sda1",
                "path": "/dev/sda1",
                "size": 4000000000,
                "fstype": "btrfs",
                "mountpoint": None,
                "model": None,
                "label": "",
                "tran": None,
                "type": "part",
            }
        ],
    }
    mock_run.return_value = _make_run_result(stdout=_lsblk_output([usb]))
    devices = detect_devices()
    assert len(devices) == 1
    assert devices[0]["supported"] is False


# ===========================================================================
# _device_info
# ===========================================================================


def test_device_info_builds_dict():
    part = {
        "name": "sda1",
        "path": "/dev/sda1",
        "size": 1073741824,
        "fstype": "ext4",
        "mountpoint": "/mnt/usb",
        "label": "DATA",
    }
    parent = {"model": "  SanDisk  "}
    info = _device_info(part, parent)

    assert info["name"] == "sda1"
    assert info["path"] == "/dev/sda1"
    assert info["size_bytes"] == 1073741824
    assert info["size"] == "1.0 GB"
    assert info["fstype"] == "ext4"
    assert info["mountpoint"] == "/mnt/usb"
    assert info["model"] == "SanDisk"
    assert info["label"] == "DATA"
    assert info["supported"] is True


def test_device_info_missing_fields():
    """Missing or None fields get safe defaults."""
    part = {"name": "sdb1"}
    parent = {}
    info = _device_info(part, parent)

    assert info["name"] == "sdb1"
    assert info["path"] == "/dev/sdb1"
    assert info["size_bytes"] == 0
    assert info["fstype"] == ""
    assert info["mountpoint"] == ""
    assert info["model"] == "USB Drive"
    assert info["label"] == ""
    assert info["supported"] is False


def test_device_info_null_mountpoint():
    """None mountpoint converts to empty string."""
    part = {
        "name": "sda1",
        "path": "/dev/sda1",
        "size": 100,
        "fstype": "vfat",
        "mountpoint": None,
        "label": None,
    }
    parent = {"model": None}
    info = _device_info(part, parent)
    assert info["mountpoint"] == ""
    assert info["label"] == ""
    assert info["model"] == "USB Drive"


# ===========================================================================
# _human_size
# ===========================================================================


def test_human_size_bytes():
    assert _human_size(0) == "0.0 B"
    assert _human_size(512) == "512.0 B"
    assert _human_size(1023) == "1023.0 B"


def test_human_size_kb():
    assert _human_size(1024) == "1.0 KB"
    assert _human_size(1536) == "1.5 KB"


def test_human_size_mb():
    assert _human_size(1048576) == "1.0 MB"
    assert _human_size(10 * 1024 * 1024) == "10.0 MB"


def test_human_size_gb():
    assert _human_size(1073741824) == "1.0 GB"
    assert _human_size(32 * 1024**3) == "32.0 GB"


def test_human_size_tb():
    assert _human_size(1024**4) == "1.0 TB"
    assert _human_size(2 * 1024**4) == "2.0 TB"


def test_human_size_pb():
    assert _human_size(1024**5) == "1.0 PB"


# ===========================================================================
# is_mounted
# ===========================================================================


@patch("monitor.services.usb.subprocess.run")
def test_is_mounted_true(mock_run):
    mock_run.return_value = _make_run_result(returncode=0)
    assert is_mounted("/mnt/usb") is True
    mock_run.assert_called_once_with(
        ["mountpoint", "-q", "/mnt/usb"],
        capture_output=True,
        timeout=5,
    )


@patch("monitor.services.usb.subprocess.run")
def test_is_mounted_false(mock_run):
    mock_run.return_value = _make_run_result(returncode=1)
    assert is_mounted("/mnt/usb") is False


@patch("monitor.services.usb.subprocess.run")
def test_is_mounted_default_mount_point(mock_run):
    mock_run.return_value = _make_run_result(returncode=0)
    assert is_mounted() is True
    mock_run.assert_called_once_with(
        ["mountpoint", "-q", DEFAULT_MOUNT_POINT],
        capture_output=True,
        timeout=5,
    )


@patch("monitor.services.usb.subprocess.run")
def test_is_mounted_timeout(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="mountpoint", timeout=5)
    assert is_mounted() is False


@patch("monitor.services.usb.subprocess.run")
def test_is_mounted_os_error(mock_run):
    mock_run.side_effect = OSError("not found")
    assert is_mounted() is False


# ===========================================================================
# mount_device
# ===========================================================================


@patch("monitor.services.usb._get_fstype_blkid", return_value="ext4")
@patch("monitor.services.usb.is_mounted", return_value=False)
@patch("monitor.services.usb.subprocess.run")
@patch("monitor.services.usb.os.makedirs")
def test_mount_device_success(mock_makedirs, mock_run, mock_is_mounted, mock_fstype):
    mock_run.return_value = _make_run_result(returncode=0)
    ok, err = mount_device("/dev/sda1", "/mnt/usb")

    assert ok is True
    assert err == ""
    mock_makedirs.assert_called_once_with("/mnt/usb", exist_ok=True)


@patch("monitor.services.usb.is_mounted", return_value=True)
@patch("monitor.services.usb.os.makedirs")
def test_mount_device_already_mounted(mock_makedirs, mock_is_mounted):
    ok, err = mount_device("/dev/sda1", "/mnt/usb")
    assert ok is True
    assert err == ""


@patch("monitor.services.usb._get_fstype_blkid", return_value="ext4")
@patch("monitor.services.usb.is_mounted", return_value=False)
@patch("monitor.services.usb.subprocess.run")
@patch("monitor.services.usb.os.makedirs")
def test_mount_device_failure(mock_makedirs, mock_run, mock_is_mounted, mock_fstype):
    mock_run.return_value = _make_run_result(
        returncode=1, stderr="mount: permission denied"
    )
    ok, err = mount_device("/dev/sda1")

    assert ok is False
    assert "permission denied" in err


@patch("monitor.services.usb._get_fstype_blkid", return_value="ext4")
@patch("monitor.services.usb.is_mounted", return_value=False)
@patch("monitor.services.usb.subprocess.run")
@patch("monitor.services.usb.os.makedirs")
def test_mount_device_failure_empty_stderr(
    mock_makedirs, mock_run, mock_is_mounted, mock_fstype
):
    """Empty stderr falls back to 'Mount failed'."""
    mock_run.return_value = _make_run_result(returncode=1, stderr="")
    ok, err = mount_device("/dev/sda1")
    assert ok is False
    assert err == "Mount failed"


@patch("monitor.services.usb._get_fstype_blkid", return_value="ext4")
@patch("monitor.services.usb.is_mounted", return_value=False)
@patch("monitor.services.usb.subprocess.run")
@patch("monitor.services.usb.os.makedirs")
def test_mount_device_timeout(mock_makedirs, mock_run, mock_is_mounted, mock_fstype):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="mount", timeout=30)
    ok, err = mount_device("/dev/sda1")
    assert ok is False
    assert err  # contains timeout info


@patch("monitor.services.usb.os.makedirs")
def test_mount_device_makedirs_os_error(mock_makedirs):
    mock_makedirs.side_effect = OSError("read-only filesystem")
    ok, err = mount_device("/dev/sda1")
    assert ok is False
    assert "read-only" in err


@patch("monitor.services.usb._get_fstype_blkid", return_value="ext4")
@patch("monitor.services.usb.is_mounted", return_value=False)
@patch("monitor.services.usb.subprocess.run")
@patch("monitor.services.usb.os.makedirs")
def test_mount_device_default_mount_point(
    mock_makedirs, mock_run, mock_is_mounted, mock_fstype
):
    mock_run.return_value = _make_run_result(returncode=0)
    ok, err = mount_device("/dev/sda1")
    assert ok is True
    mock_makedirs.assert_called_once_with(DEFAULT_MOUNT_POINT, exist_ok=True)


@patch("monitor.services.usb._get_fstype_blkid", return_value="exfat")
@patch("monitor.services.usb.is_mounted", return_value=False)
@patch("monitor.services.usb.subprocess.run")
@patch("monitor.services.usb.os.makedirs")
def test_mount_device_exfat_uses_uid_gid(
    mock_makedirs, mock_run, mock_is_mounted, mock_fstype
):
    """exFAT mount includes uid/gid/umask options."""
    mock_run.return_value = _make_run_result(returncode=0)
    ok, err = mount_device("/dev/sda1", "/mnt/usb")
    assert ok is True
    # Verify mount command includes uid/gid options
    call_args = mock_run.call_args[0][0]
    assert "-o" in call_args
    opts = call_args[call_args.index("-o") + 1]
    assert "uid=" in opts
    assert "umask=0002" in opts


# ===========================================================================
# unmount_device
# ===========================================================================


@patch("monitor.services.usb.is_mounted", return_value=True)
@patch("monitor.services.usb.subprocess.run")
def test_unmount_device_success(mock_run, mock_is_mounted):
    mock_run.return_value = _make_run_result(returncode=0)
    ok, err = unmount_device("/mnt/usb")

    assert ok is True
    assert err == ""
    mock_run.assert_called_once_with(
        ["umount", "/mnt/usb"],
        capture_output=True,
        text=True,
        timeout=30,
    )


@patch("monitor.services.usb.is_mounted", return_value=False)
def test_unmount_device_not_mounted(mock_is_mounted):
    """Unmounting a not-mounted path succeeds silently."""
    ok, err = unmount_device("/mnt/usb")
    assert ok is True
    assert err == ""


@patch("monitor.services.usb.is_mounted", return_value=True)
@patch("monitor.services.usb.subprocess.run")
def test_unmount_device_failure(mock_run, mock_is_mounted):
    mock_run.return_value = _make_run_result(returncode=1, stderr="target is busy")
    ok, err = unmount_device("/mnt/usb")
    assert ok is False
    assert "busy" in err


@patch("monitor.services.usb.is_mounted", return_value=True)
@patch("monitor.services.usb.subprocess.run")
def test_unmount_device_failure_empty_stderr(mock_run, mock_is_mounted):
    mock_run.return_value = _make_run_result(returncode=1, stderr="")
    ok, err = unmount_device()
    assert ok is False
    assert err == "Unmount failed"


@patch("monitor.services.usb.is_mounted", return_value=True)
@patch("monitor.services.usb.subprocess.run")
def test_unmount_device_timeout(mock_run, mock_is_mounted):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="umount", timeout=30)
    ok, err = unmount_device()
    assert ok is False
    assert err


@patch("monitor.services.usb.is_mounted", return_value=True)
@patch("monitor.services.usb.subprocess.run")
def test_unmount_device_default_mount_point(mock_run, mock_is_mounted):
    mock_run.return_value = _make_run_result(returncode=0)
    ok, err = unmount_device()
    assert ok is True
    mock_run.assert_called_once_with(
        ["umount", DEFAULT_MOUNT_POINT],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ===========================================================================
# format_device
# ===========================================================================


@patch("monitor.services.usb.unmount_device")
@patch("monitor.services.usb.subprocess.run")
def test_format_device_success(mock_run, mock_unmount):
    # First call: lsblk check mountpoint (not mounted)
    # Second call: mkfs.ext4
    mock_run.side_effect = [
        _make_run_result(returncode=0, stdout=""),  # lsblk — not mounted
        _make_run_result(returncode=0),  # mkfs.ext4
    ]
    ok, err = format_device("/dev/sda1")

    assert ok is True
    assert err == ""
    # mkfs.ext4 called with correct args
    mkfs_call = mock_run.call_args_list[1]
    assert mkfs_call[0][0] == ["mkfs.ext4", "-F", "-L", "HomeMonitor", "/dev/sda1"]
    mock_unmount.assert_not_called()


@patch("monitor.services.usb.unmount_device")
@patch("monitor.services.usb.subprocess.run")
def test_format_device_unmounts_first(mock_run, mock_unmount):
    """If device is mounted, unmount before formatting."""
    mock_run.side_effect = [
        _make_run_result(returncode=0, stdout="/mnt/usb\n"),  # lsblk — mounted
        _make_run_result(returncode=0),  # mkfs.ext4
    ]
    ok, err = format_device("/dev/sda1")

    assert ok is True
    mock_unmount.assert_called_once_with("/mnt/usb")


@patch("monitor.services.usb.subprocess.run")
def test_format_device_custom_label(mock_run):
    mock_run.side_effect = [
        _make_run_result(returncode=0, stdout=""),
        _make_run_result(returncode=0),
    ]
    ok, err = format_device("/dev/sda1", label="MyDisk")
    assert ok is True
    mkfs_call = mock_run.call_args_list[1]
    assert mkfs_call[0][0] == ["mkfs.ext4", "-F", "-L", "MyDisk", "/dev/sda1"]


def test_format_device_mmcblk_blocked():
    """Safety check prevents formatting SD card."""
    ok, err = format_device("/dev/mmcblk0p1")
    assert ok is False
    assert "SD card" in err


def test_format_device_mmcblk_whole_device():
    ok, err = format_device("/dev/mmcblk0")
    assert ok is False
    assert "SD card" in err


@patch("monitor.services.usb.subprocess.run")
def test_format_device_mkfs_failure(mock_run):
    mock_run.side_effect = [
        _make_run_result(returncode=0, stdout=""),
        _make_run_result(returncode=1, stderr="mkfs.ext4: device is busy"),
    ]
    ok, err = format_device("/dev/sda1")
    assert ok is False
    assert "busy" in err


@patch("monitor.services.usb.subprocess.run")
def test_format_device_mkfs_empty_stderr(mock_run):
    mock_run.side_effect = [
        _make_run_result(returncode=0, stdout=""),
        _make_run_result(returncode=1, stderr=""),
    ]
    ok, err = format_device("/dev/sda1")
    assert ok is False
    assert err == "Format failed"


@patch("monitor.services.usb.subprocess.run")
def test_format_device_timeout(mock_run):
    mock_run.side_effect = [
        _make_run_result(returncode=0, stdout=""),
        subprocess.TimeoutExpired(cmd="mkfs.ext4", timeout=120),
    ]
    ok, err = format_device("/dev/sda1")
    assert ok is False
    assert err


@patch("monitor.services.usb.subprocess.run")
def test_format_device_os_error(mock_run):
    mock_run.side_effect = OSError("mkfs.ext4 not found")
    ok, err = format_device("/dev/sda1")
    assert ok is False
    assert "not found" in err


# ===========================================================================
# prepare_recordings_dir
# ===========================================================================


@patch("monitor.services.usb.os.makedirs")
def test_prepare_recordings_dir_default(mock_makedirs):
    import os

    result = prepare_recordings_dir()
    expected = os.path.join(DEFAULT_MOUNT_POINT, RECORDINGS_FOLDER)
    assert result == expected
    mock_makedirs.assert_called_once_with(expected, exist_ok=True)


@patch("monitor.services.usb.os.makedirs")
def test_prepare_recordings_dir_custom_mount(mock_makedirs):
    import os

    result = prepare_recordings_dir("/mnt/usb")
    expected = os.path.join("/mnt/usb", RECORDINGS_FOLDER)
    assert result == expected
    mock_makedirs.assert_called_once_with(expected, exist_ok=True)


@patch("monitor.services.usb.os.makedirs")
def test_prepare_recordings_dir_returns_correct_path(mock_makedirs):
    """Returned path uses os.path.join correctly."""
    result = prepare_recordings_dir("/mnt/external")
    assert RECORDINGS_FOLDER in result
    assert result.startswith("/mnt/external")


# ===========================================================================
# Constants
# ===========================================================================


def test_supported_fs_values():
    """Verify the supported filesystem set."""
    assert {"ext4", "ext3", "ntfs", "vfat", "exfat"} == SUPPORTED_FS


def test_default_mount_point():
    assert DEFAULT_MOUNT_POINT == "/mnt/recordings"


def test_recordings_folder():
    assert RECORDINGS_FOLDER == "home-monitor-recordings"
