"""
System health monitor — collects server metrics.

Metrics collected every 10 seconds:
- CPU temperature (from /sys/class/thermal/)
- CPU usage percentage
- RAM usage (total, used, free)
- Disk usage on /data partition
- Network interfaces (IP, status)
- System uptime

Warning thresholds:
- CPU temp > 70C
- Disk usage > 85%
- RAM usage > 90%
"""

# TODO: Implement HealthMonitor class
