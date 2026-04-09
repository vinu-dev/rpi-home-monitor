"""
Background services that run as threads within the Flask process.

Services:
  RecorderService  - manages ffmpeg processes for clip recording
  DiscoveryService - scans for cameras via Avahi/mDNS
  StorageManager   - monitors disk, loop-deletes oldest clips
  HealthMonitor    - collects CPU/temp/RAM/disk metrics
  AuditLogger      - logs security events to /data/logs/audit.log
"""
