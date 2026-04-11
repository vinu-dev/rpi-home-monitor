"""
Application services — business logic layer.

Each service has a single responsibility and receives dependencies
via constructor injection. Routes are thin HTTP adapters that delegate here.

Services:
  CameraService        - camera CRUD, lifecycle, streaming coordination
  UserService          - user CRUD, password management, audit
  SettingsService      - system settings, WiFi config (post-setup)
  ProvisioningService  - first-boot setup wizard (WiFi, admin, completion)
  StorageService       - USB select/format/eject orchestration
  StorageManager       - FIFO loop recording cleanup, disk monitoring
  StreamingService     - ffmpeg pipeline management (HLS, recording, snapshots)
  RecorderService      - clip metadata, listing, deletion
  DiscoveryService     - camera discovery via Avahi/mDNS
  AuditLogger          - append-only security event log
  usb                  - USB device detection, mount, format
"""
