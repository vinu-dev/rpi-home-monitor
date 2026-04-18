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

from dataclasses import dataclass, field


@dataclass
class Camera:
    """Represents a camera node (paired or pending)."""

    id: str  # Derived from hardware serial
    name: str = ""  # User-assigned name (e.g., "Front Door")
    location: str = ""  # User-assigned location (e.g., "Outdoor")
    status: str = "pending"  # pending | online | offline
    ip: str = ""
    rtsp_url: str = ""
    # ADR-0017: recording mode + schedule (off default — on-demand streaming)
    recording_mode: str = "off"  # off | continuous | schedule | motion
    recording_schedule: list[dict] = field(default_factory=list)
    recording_motion_enabled: bool = False  # reserved for future motion ADR
    # Server's mirror of what it last asked the camera to do (ADR-0017)
    desired_stream_state: str = "stopped"  # running | stopped
    resolution: str = "1080p"  # 720p | 1080p
    fps: int = 25
    paired_at: str | None = None
    last_seen: str | None = None
    firmware_version: str = ""
    cert_serial: str = ""
    pairing_secret: str = ""  # hex-encoded, for camera LUKS key derivation (ADR-0010)
    # Stream parameters (ADR-0015: server-camera control channel)
    width: int = 1920
    height: int = 1080
    bitrate: int = 4000000
    h264_profile: str = "high"
    keyframe_interval: int = 30
    rotation: int = 0
    hflip: bool = False
    vflip: bool = False
    config_sync: str = "unknown"  # synced | pending | error | unknown
    # Live status fields — populated by heartbeat (ADR-0016)
    streaming: bool = False  # is camera actively streaming RTSP?
    cpu_temp: float = 0.0  # °C, from last heartbeat
    memory_percent: int = 0  # 0-100, from last heartbeat
    uptime_seconds: int = 0  # seconds since camera boot


@dataclass
class User:
    """System user account."""

    id: str
    username: str
    password_hash: str  # bcrypt, cost 12
    role: str = "viewer"  # admin | viewer
    created_at: str = ""
    last_login: str | None = None
    totp_secret: str = ""  # TOTP secret for 2FA (ADR-0011, future)
    failed_logins: int = 0  # consecutive failed login count
    locked_until: str = ""  # ISO timestamp, empty = not locked
    must_change_password: bool = False  # force password change on next login


@dataclass
class Settings:
    """System-wide settings. Persisted to /data/config/settings.json."""

    timezone: str = "Europe/Dublin"
    # Time sync (ADR-0019). ntp_mode=auto → systemd-timesyncd/timedatectl
    # pulls from configured NTP servers. ntp_mode=manual → NTP disabled,
    # clock stays where the user set it via /settings/time. Persisted on
    # /data so OTA rootfs swaps preserve the user's choice.
    ntp_mode: str = "auto"  # auto | manual
    storage_threshold_percent: int = 90
    clip_duration_seconds: int = 180
    session_timeout_minutes: int = 30
    hostname: str = "home-monitor"
    setup_completed: bool = False
    firmware_version: str = "1.0.0"
    # USB storage — set when user selects a USB device for recordings
    usb_device: str = ""  # e.g. /dev/sda1 (empty = internal)
    usb_recordings_dir: str = ""  # e.g. /mnt/recordings/home-monitor-recordings
    # Tailscale VPN configuration
    tailscale_enabled: bool = False  # enable/disable tailscaled daemon
    tailscale_auto_connect: bool = False  # auto-run 'tailscale up' on boot
    tailscale_accept_routes: bool = False  # --accept-routes flag
    tailscale_ssh: bool = False  # --ssh flag for Tailscale SSH
    tailscale_auth_key: str = ""  # pre-auth key for headless setup
    # Loop-recording watermarks (ADR-0017). LoopRecorder deletes oldest
    # recording segments when free space drops below low_watermark %
    # and stops deleting once free space reaches low + hysteresis.
    loop_low_watermark_percent: int = 10
    loop_hysteresis_percent: int = 5


@dataclass
class Clip:
    """Represents a single recorded video clip."""

    camera_id: str
    filename: str  # HH-MM-SS.mp4
    date: str  # YYYY-MM-DD
    start_time: str  # HH:MM:SS  (UTC, matches filename timestamp)
    duration_seconds: int = 180
    size_bytes: int = 0
    thumbnail: str = ""  # HH-MM-SS.thumb.jpg

    @property
    def started_at(self) -> str:
        """UTC ISO-8601 of clip start. Filenames are written in UTC
        on the camera; attach a ``Z`` so the browser doesn't read them
        as local time and show every clip as ``<offset>h ago``."""
        if self.date and self.start_time:
            return f"{self.date}T{self.start_time}Z"
        return ""
