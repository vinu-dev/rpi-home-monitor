"""
Application services — business logic layer.

Each service has a single responsibility and receives dependencies
via constructor injection. Routes are thin HTTP adapters that delegate here.

Naming convention: all service files end with _service.py or _manager.py.

Services:
  camera_service.py       - CameraService: camera CRUD, lifecycle, streaming coordination
  user_service.py         - UserService: user CRUD, password management, audit
  settings_service.py     - SettingsService: system settings, WiFi config (post-setup)
  provisioning_service.py - ProvisioningService: first-boot setup wizard
  storage_service.py      - StorageService: USB select/format/eject orchestration
  storage_manager.py      - StorageManager: FIFO loop recording cleanup, disk monitoring
  streaming_service.py    - StreamingService: ffmpeg pipeline management (HLS, recording, snapshots)
  recorder_service.py     - RecorderService: clip metadata, listing, deletion
  discovery.py            - DiscoveryService: camera discovery via Avahi/mDNS
  audit.py                - AuditLogger: append-only security event log
  health.py               - HealthService: CPU temp, RAM, disk, uptime
  usb.py                  - USB device detection, mount, format
"""
