# REQ: SWR-037, SWR-032; RISK: RISK-008, RISK-022; SEC: SC-020; TEST: TC-035, TC-029
"""
System health monitor — collects server metrics.

Metrics collected:
- CPU temperature (from /sys/class/thermal/)
- CPU usage percentage
- RAM usage (total, used, free)
- Disk usage on /data partition
- System uptime

Warning thresholds:
- CPU temp > 70C
- Disk usage > 85%
- RAM usage > 90%
"""

import shutil
import time
from pathlib import Path

# Cached CPU sample for delta calculation between calls
_prev_cpu_sample: tuple[float, ...] | None = None
_prev_cpu_time: float = 0.0


def get_cpu_temperature() -> float:
    """Read CPU temperature in Celsius from sysfs.

    Returns 0.0 if not available (e.g., non-RPi systems).
    """
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        raw = thermal_path.read_text().strip()
        return int(raw) / 1000.0
    except (OSError, ValueError):
        return 0.0


def _read_cpu_times() -> tuple[float, ...] | None:
    """Read aggregate CPU times from /proc/stat.

    Returns (user, nice, system, idle, iowait, irq, softirq, steal)
    or None if unavailable.
    """
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        return tuple(map(float, line.split()[1:8]))
    except (OSError, ValueError, IndexError):
        return None


def get_cpu_usage() -> float:
    """Get CPU usage percentage since last call.

    Compares /proc/stat counters between two successive calls. The
    first call returns 0.0 (no previous sample) and caches a baseline.
    Subsequent calls return the delta-based usage percentage.
    """
    global _prev_cpu_sample, _prev_cpu_time

    current = _read_cpu_times()
    if current is None:
        return 0.0

    now = time.monotonic()
    prev = _prev_cpu_sample
    _prev_cpu_sample = current
    _prev_cpu_time = now

    if prev is None:
        return 0.0

    # Delta between samples: user, nice, system, idle, iowait, irq, softirq
    deltas = tuple(c - p for c, p in zip(current, prev, strict=False))
    total = sum(deltas)
    if total <= 0:
        return 0.0

    idle = deltas[3]  # idle column
    usage = ((total - idle) / total) * 100
    return round(min(usage, 100.0), 1)


def get_memory_info() -> dict:
    """Get RAM usage info.

    Returns dict with total_mb, used_mb, free_mb, percent.
    """
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        info = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(":")] = int(parts[1])

        total_kb = info.get("MemTotal", 0)
        available_kb = info.get("MemAvailable", 0)
        total_mb = total_kb // 1024
        available_mb = available_kb // 1024
        used_mb = total_mb - available_mb
        percent = (used_mb / total_mb * 100) if total_mb > 0 else 0.0

        return {
            "total_mb": total_mb,
            "used_mb": used_mb,
            "free_mb": available_mb,
            "percent": round(percent, 1),
        }
    except (OSError, ValueError, KeyError):
        return {"total_mb": 0, "used_mb": 0, "free_mb": 0, "percent": 0.0}


def get_disk_usage(path: str = "/data") -> dict:
    """Get disk usage for a partition.

    Returns dict with total_gb, used_gb, free_gb, percent.
    """
    try:
        usage = shutil.disk_usage(path)
        total_gb = round(usage.total / (1024**3), 1)
        used_gb = round(usage.used / (1024**3), 1)
        free_gb = round(usage.free / (1024**3), 1)
        percent = round(usage.used / usage.total * 100, 1) if usage.total > 0 else 0.0
        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
        }
    except OSError:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0.0}


def get_uptime() -> dict:
    """Get system uptime.

    Returns dict with seconds and human-readable string.
    """
    try:
        raw = Path("/proc/uptime").read_text().strip()
        seconds = int(float(raw.split()[0]))
    except (OSError, ValueError, IndexError):
        seconds = 0

    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")

    return {
        "seconds": seconds,
        "display": " ".join(parts),
    }


def get_network_info() -> list[dict]:
    """Get active network interfaces with IP addresses.

    Returns a list of dicts with name, ip, mac, and type.
    Reads from /sys/class/net/. Linux-only (uses fcntl ioctl for
    IP address lookup). Returns empty list on non-Linux systems.
    """
    try:
        import fcntl
    except ImportError:
        return []  # Not on Linux (CI, Windows dev machine)

    import socket
    import struct

    interfaces = []
    net_dir = Path("/sys/class/net")
    if not net_dir.is_dir():
        return interfaces

    for iface in sorted(net_dir.iterdir()):
        name = iface.name
        if name == "lo":
            continue

        info: dict = {"name": name, "ip": "", "mac": "", "type": ""}

        # Determine type
        if name.startswith("wlan") or name.startswith("wlp"):
            info["type"] = "wifi"
        elif name.startswith("eth") or name.startswith("enp"):
            info["type"] = "ethernet"
        elif name.startswith("tailscale"):
            info["type"] = "vpn"
        else:
            info["type"] = "other"

        # Read MAC address
        try:
            info["mac"] = (iface / "address").read_text().strip()
        except OSError:
            pass

        # Get IPv4 address via ioctl
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            result = fcntl.ioctl(
                sock.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack("256s", name.encode()[:15]),
            )
            info["ip"] = socket.inet_ntoa(result[20:24])
            sock.close()
        except OSError:
            pass

        # Only include interfaces that are UP
        try:
            flags = (iface / "flags").read_text().strip()
            if int(flags, 16) & 0x1 == 0:  # IFF_UP
                continue
        except (OSError, ValueError):
            pass

        if info["ip"]:
            interfaces.append(info)

    return interfaces


def get_health_summary(data_dir: str = "/data") -> dict:
    """Collect all health metrics in one call.

    Returns a dict with cpu_temp, cpu_usage, memory, disk, uptime, and warnings.
    """
    cpu_temp = get_cpu_temperature()
    memory = get_memory_info()
    disk = get_disk_usage(data_dir)
    uptime = get_uptime()

    warnings = []
    if cpu_temp > 70:
        warnings.append(f"CPU temperature high: {cpu_temp}°C")
    if disk["percent"] > 85:
        warnings.append(f"Disk usage high: {disk['percent']}%")
    if memory["percent"] > 90:
        warnings.append(f"RAM usage high: {memory['percent']}%")

    return {
        "cpu_temp_c": cpu_temp,
        "cpu_usage_percent": get_cpu_usage(),
        "memory": memory,
        "disk": disk,
        "network": get_network_info(),
        "uptime": uptime,
        "warnings": warnings,
        "status": "warning" if warnings else "healthy",
    }
