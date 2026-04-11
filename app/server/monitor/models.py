"""
Data models for the monitoring system.

All models are stored as JSON files on the /data partition.
No database — the data volume is small (dozens of cameras,
handful of users) and JSON is human-inspectable.

Files:
  /data/config/cameras.json  - camera registry
  /data/config/users.json    - user accounts
  /data/config/settings.json - system settings
"""

from dataclasses import dataclass


@dataclass
class Camera:
    """Represents a camera node (paired or pending)."""

    id: str  # Derived from hardware serial
    name: str = ""  # User-assigned name (e.g., "Front Door")
    location: str = ""  # User-assigned location (e.g., "Outdoor")
    status: str = "pending"  # pending | online | offline
    ip: str = ""
    rtsp_url: str = ""
    recording_mode: str = "continuous"  # continuous | off | motion (Phase 2)
    resolution: str = "1080p"  # 720p | 1080p
    fps: int = 25
    paired_at: str | None = None
    last_seen: str | None = None
    firmware_version: str = ""
    cert_serial: str = ""
    pairing_secret: str = ""  # hex-encoded, for camera LUKS key derivation (ADR-0010)


@dataclass
class User:
    """System user account."""

    id: str
    username: str
    password_hash: str  # bcrypt, cost 12
    role: str = "viewer"  # admin | viewer
    created_at: str = ""
    last_login: str | None = None


@dataclass
class Settings:
    """System-wide settings. Persisted to /data/config/settings.json."""

    timezone: str = "Europe/Dublin"
    storage_threshold_percent: int = 90
    clip_duration_seconds: int = 180
    session_timeout_minutes: int = 30
    hostname: str = "home-monitor"
    setup_completed: bool = False
    firmware_version: str = "1.0.0"
    # USB storage — set when user selects a USB device for recordings
    usb_device: str = ""  # e.g. /dev/sda1 (empty = internal)
    usb_recordings_dir: str = ""  # e.g. /mnt/recordings/home-monitor-recordings


@dataclass
class Clip:
    """Represents a single recorded video clip."""

    camera_id: str
    filename: str  # HH-MM-SS.mp4
    date: str  # YYYY-MM-DD
    start_time: str  # HH:MM:SS
    duration_seconds: int = 180
    size_bytes: int = 0
    thumbnail: str = ""  # HH-MM-SS.thumb.jpg
