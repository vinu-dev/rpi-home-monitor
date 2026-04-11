"""
USB storage detection and management.

Detects USB block devices, checks filesystem compatibility, mounts/unmounts
for use as external recording storage. Supports ext4, ext3, ntfs, vfat, exfat.

Unsupported filesystems are formatted to ext4 with user confirmation.
"""
import logging
import os
import subprocess
import json

log = logging.getLogger("monitor.usb")

SUPPORTED_FS = {"ext4", "ext3", "ntfs", "vfat", "exfat"}
DEFAULT_MOUNT_POINT = "/mnt/recordings"
RECORDINGS_FOLDER = "home-monitor-recordings"


def detect_devices() -> list[dict]:
    """Detect USB block devices.

    Returns list of dicts: {name, path, size, size_bytes, fstype,
    mountpoint, model, label, supported}.
    """
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-b", "-o",
             "NAME,PATH,SIZE,FSTYPE,MOUNTPOINT,MODEL,LABEL,TRAN,TYPE"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            log.warning("lsblk failed: %s", result.stderr.strip())
            return []

        data = json.loads(result.stdout)
        devices = []

        for dev in data.get("blockdevices", []):
            # Look for USB devices (tran=usb) or their partitions
            if dev.get("tran") == "usb":
                # Check partitions of this USB device
                children = dev.get("children", [])
                if children:
                    for part in children:
                        if part.get("type") == "part":
                            devices.append(_device_info(part, dev))
                elif dev.get("type") in ("disk", "part"):
                    # Whole device with no partitions
                    devices.append(_device_info(dev, dev))

        return devices

    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        log.error("USB detection failed: %s", e)
        return []


def _device_info(part, parent):
    """Build device info dict from lsblk JSON."""
    fstype = part.get("fstype") or ""
    device_path = part.get("path", f"/dev/{part.get('name', '')}")

    # lsblk may not report fstype for non-root users — fall back to blkid
    if not fstype and device_path:
        fstype = _get_fstype_blkid(device_path)

    size_bytes = int(part.get("size") or 0)
    return {
        "name": part.get("name", ""),
        "path": device_path,
        "size": _human_size(size_bytes),
        "size_bytes": size_bytes,
        "fstype": fstype,
        "mountpoint": part.get("mountpoint") or "",
        "model": (parent.get("model") or "USB Drive").strip(),
        "label": part.get("label") or "",
        "supported": fstype.lower() in SUPPORTED_FS,
    }


def _get_fstype_blkid(device_path):
    """Get filesystem type via blkid (works for non-root users).

    Falls back gracefully if blkid is unavailable or fails.
    """
    try:
        result = subprocess.run(
            ["blkid", "-s", "TYPE", "-o", "value", device_path],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _human_size(nbytes):
    """Convert bytes to human-readable size string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} PB"


def is_mounted(mount_point=DEFAULT_MOUNT_POINT) -> bool:
    """Check if a mount point is currently mounted."""
    try:
        result = subprocess.run(
            ["mountpoint", "-q", mount_point],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def mount_device(device_path, mount_point=DEFAULT_MOUNT_POINT) -> tuple[bool, str]:
    """Mount a USB device at the given mount point.

    Returns (success, error_message).
    """
    try:
        os.makedirs(mount_point, exist_ok=True)

        # Check if already mounted
        if is_mounted(mount_point):
            log.info("Mount point %s already mounted", mount_point)
            return True, ""

        result = subprocess.run(
            ["mount", device_path, mount_point],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "Mount failed"
            log.error("Failed to mount %s: %s", device_path, err)
            return False, err

        log.info("Mounted %s at %s", device_path, mount_point)
        return True, ""

    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


def unmount_device(mount_point=DEFAULT_MOUNT_POINT) -> tuple[bool, str]:
    """Unmount a USB device.

    Returns (success, error_message).
    """
    if not is_mounted(mount_point):
        return True, ""

    try:
        result = subprocess.run(
            ["umount", mount_point],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "Unmount failed"
            log.error("Failed to unmount %s: %s", mount_point, err)
            return False, err

        log.info("Unmounted %s", mount_point)
        return True, ""

    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


def format_device(device_path, fstype="ext4",
                  label="HomeMonitor") -> tuple[bool, str]:
    """Format a USB device to ext4.

    WARNING: This destroys all data on the device.
    Returns (success, error_message).
    """
    # Safety: never format mmcblk (SD card) or system disks
    if "mmcblk" in device_path:
        return False, "Cannot format SD card"

    try:
        # Unmount first if mounted
        result = subprocess.run(
            ["lsblk", "-no", "MOUNTPOINT", device_path],
            capture_output=True, text=True, timeout=5,
        )
        mp = result.stdout.strip()
        if mp:
            unmount_device(mp)

        # Format
        cmd = ["mkfs.ext4", "-F", "-L", label, device_path]
        log.info("Formatting %s as ext4 (label=%s)", device_path, label)
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "Format failed"
            log.error("Format failed: %s", err)
            return False, err

        log.info("Formatted %s as ext4", device_path)
        return True, ""

    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


def prepare_recordings_dir(mount_point=DEFAULT_MOUNT_POINT) -> str:
    """Create the recordings folder on a mounted USB device.

    Returns the full path to the recordings directory.
    """
    rec_dir = os.path.join(mount_point, RECORDINGS_FOLDER)
    os.makedirs(rec_dir, exist_ok=True)
    log.info("Recordings directory ready: %s", rec_dir)
    return rec_dir
