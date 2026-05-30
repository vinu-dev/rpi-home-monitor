"""
USB storage detection and management.

Detects USB block devices, checks filesystem compatibility, mounts/unmounts
for use as external recording storage. Supports ext4, ext3, ntfs, vfat, exfat.

Unsupported filesystems are formatted to ext4 with user confirmation.
"""

import json
import logging
import os
import subprocess

from monitor.services import privileged

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
            [
                "lsblk",
                "-J",
                "-b",
                "-o",
                "NAME,PATH,SIZE,FSTYPE,MOUNTPOINT,MOUNTPOINTS,MODEL,LABEL,UUID,TRAN,TYPE",
            ],
            capture_output=True,
            text=True,
            timeout=10,
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
    label = part.get("label") or ""
    uuid = part.get("uuid") or ""

    # lsblk may not report fstype for non-root users — fall back to blkid
    if device_path and (not fstype or not label or not uuid):
        props = _get_blkid_properties(device_path)
        fstype = fstype or props.get("TYPE", "")
        label = label or props.get("LABEL", "")
        uuid = uuid or props.get("UUID", "")

    size_bytes = int(part.get("size") or 0)
    filesystem_status = _filesystem_status(fstype)
    return {
        "name": part.get("name", ""),
        "path": device_path,
        "size": _human_size(size_bytes),
        "size_bytes": size_bytes,
        "fstype": fstype,
        "mountpoint": _mountpoint(part),
        "model": (parent.get("model") or "USB Drive").strip(),
        "label": label,
        "uuid": uuid,
        "filesystem_status": filesystem_status,
        "supported": filesystem_status == "supported",
    }


def _get_fstype_blkid(device_path):
    """Get filesystem type via blkid (works for non-root users).

    Falls back gracefully if blkid is unavailable or fails.
    """
    return _get_blkid_properties(device_path).get("TYPE", "")


def _get_blkid_properties(device_path):
    """Return blkid metadata for a device using cache and direct probing."""
    for cmd in (
        ["blkid", "-o", "export", device_path],
        ["blkid", "-p", "-o", "export", device_path],
    ):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0:
            props = _parse_blkid_export(result.stdout)
            if props:
                return props
    return {}


def _parse_blkid_export(output):
    props = {}
    for raw_line in output.splitlines():
        if "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        props[key.strip()] = value.strip()
    return props


def _mountpoint(part):
    mountpoint = part.get("mountpoint") or ""
    mountpoints = part.get("mountpoints")
    if mountpoint:
        return mountpoint
    if isinstance(mountpoints, list):
        return next((mp for mp in mountpoints if mp), "")
    if isinstance(mountpoints, str):
        return mountpoints.strip()
    return ""


def _filesystem_status(fstype):
    if not fstype:
        return "unknown"
    if fstype.lower() in SUPPORTED_FS:
        return "supported"
    return "unsupported"


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
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


# REQ: SWR-027; RISK: RISK-013; SEC: SC-013; TEST: TC-024
def mount_device(device_path, mount_point=DEFAULT_MOUNT_POINT) -> tuple[bool, str]:
    """Mount a USB device at the given mount point.

    For FAT-based filesystems (vfat, exfat), mounts with uid/gid of the
    current process so the monitor user can write recordings. For native
    Linux filesystems (ext4, ext3), sets ownership after mount.

    Returns (success, error_message).
    """
    if privileged.should_use_helper():
        return privileged.request_result(
            "usb.mount",
            {"device_path": device_path, "mount_point": mount_point},
        )
    try:
        os.makedirs(mount_point, exist_ok=True)

        # Check if already mounted
        if is_mounted(mount_point):
            log.info("Mount point %s already mounted", mount_point)
            return True, ""

        # Detect filesystem to set proper mount options
        fstype = _get_fstype_blkid(device_path)
        uid = getattr(os, "getuid", lambda: 0)()
        gid = getattr(os, "getgid", lambda: 0)()

        cmd = ["mount", device_path, mount_point]

        # FAT-based filesystems need uid/gid/umask at mount time
        # (they don't support POSIX ownership/chmod after mount)
        if fstype in ("vfat", "exfat") or fstype == "ntfs":
            cmd = [
                "mount",
                "-o",
                f"uid={uid},gid={gid},umask=0002",
                device_path,
                mount_point,
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "Mount failed"
            log.error("Failed to mount %s: %s", device_path, err)
            return False, err

        # For native Linux filesystems, set ownership after mount
        if fstype in ("ext4", "ext3") and hasattr(os, "chown"):
            try:
                os.chown(mount_point, uid, gid)
            except OSError as e:
                log.warning("Could not chown %s: %s", mount_point, e)

        log.info(
            "Mounted %s at %s (fstype=%s, uid=%d)",
            device_path,
            mount_point,
            fstype,
            uid,
        )
        return True, ""

    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


def unmount_device(mount_point=DEFAULT_MOUNT_POINT) -> tuple[bool, str]:
    """Unmount a USB device.

    Returns (success, error_message).
    """
    if privileged.should_use_helper():
        return privileged.request_result(
            "usb.unmount",
            {"mount_point": mount_point},
        )
    if not is_mounted(mount_point):
        return True, ""

    try:
        result = subprocess.run(
            ["umount", mount_point],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or "Unmount failed"
            log.error("Failed to unmount %s: %s", mount_point, err)
            return False, err

        log.info("Unmounted %s", mount_point)
        return True, ""

    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


# REQ: SWR-027; RISK: RISK-013; SEC: SC-013; TEST: TC-024
def format_device(device_path, fstype="ext4", label="HomeMonitor") -> tuple[bool, str]:
    """Format a USB device to ext4.

    WARNING: This destroys all data on the device.
    Returns (success, error_message).
    """
    if privileged.should_use_helper():
        return privileged.request_result(
            "usb.format",
            {"device_path": device_path, "fstype": fstype, "label": label},
            timeout=130,
        )
    # Safety: never format mmcblk (SD card) or system disks
    if "mmcblk" in device_path:
        return False, "Cannot format SD card"

    try:
        # Unmount first if mounted
        result = subprocess.run(
            ["lsblk", "-no", "MOUNTPOINT", device_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        mp = result.stdout.strip()
        if mp:
            unmount_device(mp)

        # Format
        cmd = ["mkfs.ext4", "-F", "-L", label, device_path]
        log.info("Formatting %s as ext4 (label=%s)", device_path, label)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
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
