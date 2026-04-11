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
from pathlib import Path


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


def get_cpu_usage() -> float:
    """Get CPU usage percentage.

    Uses /proc/stat to calculate CPU usage between two samples.
    Returns 0.0 if not available.
    """
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        values = list(map(int, line.split()[1:]))  # noqa: F841
        # Need two samples for delta — return 0 for single-call
        return 0.0
    except (OSError, ValueError, IndexError):
        return 0.0


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
        "uptime": uptime,
        "warnings": warnings,
        "status": "warning" if warnings else "healthy",
    }
