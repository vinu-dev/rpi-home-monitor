# REQ: SWR-023, SWR-025, SWR-033, SWR-045, SWR-056, SWR-057, SWR-066; RISK: RISK-011, RISK-015, RISK-016, RISK-017, RISK-020, RISK-021; SEC: SC-011, SC-012, SC-015, SC-020, SC-021; TEST: TC-022, TC-023, TC-030, TC-031, TC-041, TC-042, TC-048, TC-049, TC-054
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
class WebhookDestination:
    """Outbound webhook destination persisted in system settings."""

    id: str
    url: str
    auth_type: str = "none"  # none | bearer | hmac
    secret: str = ""
    custom_headers: dict[str, str] = field(default_factory=dict)
    event_classes: tuple[str, ...] = field(default_factory=tuple)
    enabled: bool = True
    created_at: str = ""
    last_delivery_at: str | None = None
    consecutive_failures: int = 0
    degraded: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.custom_headers, dict):
            self.custom_headers = {
                str(key): str(value)
                for key, value in self.custom_headers.items()
                if str(key).strip()
            }
        else:
            self.custom_headers = {}

        raw_classes = self.event_classes
        if isinstance(raw_classes, str):
            raw_classes = [raw_classes]
        if raw_classes is None:
            raw_classes = []
        if not isinstance(raw_classes, (list, tuple, set, frozenset)):
            raw_classes = []
        self.event_classes = tuple(
            sorted({str(item).strip() for item in raw_classes if str(item).strip()})
        )


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
    last_beat_camera_ts: str = ""  # camera-supplied heartbeat timestamp (UTC Z)
    firmware_version: str = ""
    cert_serial: str = ""
    pairing_secret: str = ""  # hex-encoded, for camera LUKS key derivation (ADR-0010)
    # Stream parameters (ADR-0015: server-camera control channel)
    width: int = 1920
    height: int = 1080
    bitrate: int = 4000000
    h264_profile: str = "high"
    keyframe_interval: int = 30
    encoder_preset: str = ""  # empty = Custom / hand-tuned values
    rotation: int = 0
    hflip: bool = False
    vflip: bool = False
    # Motion detection sensitivity (ADR-0021): 1 (lowest) … 10 (highest).
    # 5 = Medium is the shipping default; it catches hand-sized motion at
    # a few metres while rejecting typical indoor sensor noise. Operators
    # tune this per camera from Camera Settings; the server pushes changes
    # over the existing control channel (ADR-0015).
    motion_sensitivity: int = 5
    config_sync: str = "unknown"  # synced | pending | error | unknown
    # One-shot server→camera flags piggy-back on the existing heartbeat
    # response channel so operators can queue a camera-side action even
    # while the camera is temporarily unreachable.
    pending_config: dict = field(default_factory=dict)
    # Live status fields — populated by heartbeat (ADR-0016)
    streaming: bool = False  # is camera actively streaming RTSP?
    cpu_temp: float = 0.0  # °C, from last heartbeat
    memory_percent: int = 0  # 0-100, from last heartbeat
    uptime_seconds: int = 0  # seconds since camera boot
    throttle_state: dict | None = None  # decoded Pi throttle bits from heartbeat
    # Hardware health reported by the camera in every heartbeat.
    #
    # v1.3.0 shipped two flat fields (``hardware_ok`` +
    # ``hardware_error``). v1.3.x adds ``hardware_faults``, a
    # structured list of {code, severity, message, context} records
    # — see ``app/camera/camera_streamer/faults.py`` for the fault
    # catalogue. Keep the legacy fields populated for any consumer
    # that hasn't migrated yet; new code should prefer
    # ``hardware_faults`` so it can render per-fault severity.
    hardware_ok: bool = True
    hardware_error: str = ""
    hardware_faults: list[dict] = field(default_factory=list)
    # Camera sensor identity + supported modes — populated from the
    # heartbeat payload's ``capabilities`` block (#173). Cameras on
    # firmware older than the multi-sensor change don't include the
    # block; those records keep ``sensor_model=""`` and an empty
    # ``sensor_modes`` list and the dashboard falls back to the legacy
    # preset dropdown for them. Each mode is a dict with integer
    # ``width``, ``height`` and float ``max_fps``.
    sensor_model: str = ""
    sensor_modes: list[dict] = field(default_factory=list)
    sensor_detection_method: str = ""
    # Image-quality controls catalogue (#182). Mirrors the camera's
    # IMAGE_CONTROL_CATALOGUE: keyed by libcamera control name with
    # min/max/default/kind. Empty for cameras still on pre-#182
    # firmware; the dashboard renders a row only for keys present here.
    image_controls: dict = field(default_factory=dict)
    # User-customised image-quality values (#182). Keyed by libcamera
    # control name. Absent keys mean "no override — use libcamera
    # default". Pushed to the camera via the existing control channel.
    image_quality: dict = field(default_factory=dict)
    # Encoder ceiling the camera-board can H.264-encode (#182). Surfaced
    # so the dashboard can flag any saved mode whose pixel count now
    # exceeds what the running board can drive (e.g. sensor swap on a
    # Zero 2W to one with higher native res than the encoder supports).
    encoder_max_pixels: int = 0
    board_name: str = ""
    # Camera offline alerts (#136). Per-camera toggle so an operator
    # can silence the inbox for a known-flaky / under-maintenance
    # camera without losing the visual offline indicator on the
    # dashboard. Default True — alerting is the safe-by-default
    # state for a security product.
    offline_alerts_enabled: bool = True
    # ISO-8601 timestamp (Z) of the last CAMERA_OFFLINE audit event
    # emitted for this camera. Used to suppress flapping: a quick
    # online→offline→online→offline bounce should produce one alert
    # plus one suppressed "still flapping" event, not a stream.
    # Cleared back to "" on a clean recovery interval (see
    # OFFLINE_ALERT_COOLDOWN_SECONDS in discovery.py).
    last_offline_alert_at: str = ""
    # Rich motion notifications (#121, ADR-0027). Per-camera rule:
    #   enabled                       — opt this camera in/out of
    #                                   browser notifications.
    #   min_duration_seconds          — drop motion events shorter
    #                                   than this; sub-second flicker
    #                                   shouldn't fire a notif.
    #   coalesce_seconds              — within this window of the
    #                                   last delivered notif for the
    #                                   same camera, suppress the
    #                                   browser surface (event still
    #                                   lands in the alert-center
    #                                   inbox).
    # Defaults baked into the field type so legacy cameras.json
    # records get safe shipping defaults on first deserialize.
    notification_rule: dict = field(
        default_factory=lambda: {
            "enabled": True,
            "min_duration_seconds": 3,
            "coalesce_seconds": 60,
        }
    )
    # ISO-8601 timestamp of the last motion notification delivered
    # for this camera. Used to enforce the coalesce window.
    last_notification_at: str = ""


@dataclass
class User:
    """System user account."""

    id: str
    username: str
    password_hash: str  # bcrypt, cost 12
    role: str = "viewer"  # admin | viewer
    created_at: str = ""
    last_login: str | None = None
    totp_secret: str = ""  # TOTP secret for 2FA (ADR-0011, issue #238)
    totp_enabled: bool = False  # whether TOTP is active for this user
    recovery_code_hashes: list[str] = field(
        default_factory=list
    )  # bcrypt hashes of single-use recovery codes
    last_totp_step: int = 0  # anti-replay: last accepted TOTP step number
    failed_logins: int = 0  # consecutive failed login count
    locked_until: str = ""  # ISO timestamp, empty = not locked
    must_change_password: bool = False  # force password change on next login
    # Rich motion notifications (#121, ADR-0027). Per-user prefs:
    #   enabled                       — global on/off. Default OFF
    #                                   per spec ("ship disabled by
    #                                   default until browser
    #                                   enrollment is complete").
    #   cameras                       — partial overrides keyed by
    #                                   camera_id; values are partial
    #                                   dicts that override fields of
    #                                   the camera-level
    #                                   notification_rule.
    notification_prefs: dict = field(
        default_factory=lambda: {
            "enabled": False,
            "cameras": {},
        }
    )
    # Quiet-hours default schedule (#245). The per-user baseline;
    # camera-specific overrides live in notification_prefs.cameras[cam_id]
    # under quiet_schedule. Empty = no quiet hours.
    notification_schedule: list[dict] = field(default_factory=list)
    # Cross-session continuity for the polling client — the most
    # recent timestamp this user's browser confirmed it had delivered
    # via /notifications/seen. Subsequent polls filter by this.
    last_notification_seen_at: str = ""


@dataclass
class ActiveSession:
    """Server-side inventory row for an authenticated browser session."""

    id: str
    user_id: str
    username: str
    role: str = "viewer"
    created_at: float = 0.0
    last_active: float = 0.0
    expires_at: float = 0.0
    source_ip: str = ""
    user_agent: str = ""
    is_remember_me: bool = False


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
    # Motion detection (docs/archive/exec-plans/motion-detection.md §Phase 4).
    # When recording_mode="motion" on a camera, the RecordingScheduler
    # keeps the recorder running for this many seconds after the last
    # motion event ends — so the saved clip includes the tail of the
    # scene (the person walking out of frame, the gate closing, etc.).
    # Bump up for "I want 30 s of aftermath"; shrink to trim storage.
    motion_post_roll_seconds: int = 10
    # TOTP 2FA policy (issue #238). When enabled, sessions from Tailscale
    # Funnel IPs must present a TOTP code after password verification.
    require_2fa_for_remote: bool = False
    # Offsite backup (#243). Credentials are stored on the encrypted /data
    # volume and are never returned in plaintext from API reads.
    offsite_backup_enabled: bool = False
    offsite_backup_endpoint: str = ""
    offsite_backup_bucket: str = ""
    offsite_backup_access_key_id: str = ""
    offsite_backup_secret_access_key: str = ""
    offsite_backup_prefix: str = ""
    offsite_backup_retention_days: int | None = 30
    offsite_backup_bandwidth_cap_mbps: float | None = None
    webhook_destinations: list[WebhookDestination] = field(default_factory=list)
    webhook_delivery_history_retention_days: int = 30
    backup_max_history: int = 3

    def __post_init__(self) -> None:
        normalised: list[WebhookDestination] = []
        for item in self.webhook_destinations or []:
            if isinstance(item, WebhookDestination):
                normalised.append(item)
            elif isinstance(item, dict):
                normalised.append(WebhookDestination(**item))
        self.webhook_destinations = normalised


@dataclass
class MotionEvent:
    """A single motion detection, as surfaced by a camera.

    See `docs/archive/exec-plans/motion-detection.md`. Events are always logged
    regardless of the camera's `recording_mode`; the optional `clip_ref`
    is populated by the server when it can match the event timestamp to
    a finalised clip on disk.
    """

    id: str  # e.g. "mot-20260419T143002Z-cam-d8ee"
    camera_id: str
    started_at: str  # ISO-8601 UTC, server-side authoritative time
    ended_at: str | None = None  # None while active
    peak_score: float = 0.0  # 0.0-1.0 fraction of pixels changed
    peak_pixels_changed: int = 0
    duration_seconds: float = 0.0
    clip_ref: dict | None = None  # {camera_id, date, filename, offset_seconds}
    zones: list[dict] = field(default_factory=list)  # future motion-zone support
    version: int = 1


@dataclass
class ServerMeta:
    """Server identity stamped into exported clips."""

    hostname: str = "home-monitor"
    server_version: str = ""
    git_sha: str = ""


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
    stamped: bool = False

    @property
    def started_at(self) -> str:
        """UTC ISO-8601 of clip start. Filenames are written in UTC
        on the camera; attach a ``Z`` so the browser doesn't read them
        as local time and show every clip as ``<offset>h ago``."""
        if self.date and self.start_time:
            return f"{self.date}T{self.start_time}Z"
        return ""


@dataclass
class ShareLink:
    """Public, single-resource share link minted by an admin.

    Tokens are persisted under /data/config/share_links.json. ``resource_id``
    is:
      - ``camera_id`` for live camera shares
      - ``camera_id/YYYY-MM-DD/filename.mp4`` for clip shares

    Pinning is first-use binding:
      - ``pin_ip=True`` stores the first successful visitor IP and later
        accepts the same /24 (IPv4) or /64 (IPv6) network.
      - ``pin_ua=True`` stores a normalised browser-family signature on the
        first successful access and later requires an exact match.
    """

    token: str
    resource_type: str  # clip | camera
    resource_id: str
    owner_id: str
    owner_username: str = ""
    created_at: str = ""
    expires_at: str = ""
    revoked_at: str = ""
    note: str = ""
    pin_ip: bool = False
    pin_ua: bool = False
    pinned_ip: str = ""
    pinned_ua: str = ""
    access_count: int = 0
    first_access_at: str = ""
    last_access_at: str = ""
