"""
Background services that run as threads within the Flask process.

Services:
  StreamingService - manages ffmpeg pipelines (HLS, recording, snapshots)
  RecorderService  - manages ffmpeg processes for clip recording
  DiscoveryService - scans for cameras via Avahi/mDNS
  StorageManager   - monitors disk, loop-deletes oldest clips (FIFO)
  HealthMonitor    - collects CPU/temp/RAM/disk metrics
  AuditLogger      - logs security events to /data/logs/audit.log
  usb              - USB device detection, mount, format, auto-mount
"""
