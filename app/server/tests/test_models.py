"""Tests for data models."""

from dataclasses import asdict

from monitor.models import Camera, Clip, Settings, User


class TestCamera:
    """Test Camera dataclass."""

    def test_create_camera_minimal(self):
        cam = Camera(id="cam-001")
        assert cam.id == "cam-001"
        assert cam.name == ""
        assert cam.status == "pending"
        assert cam.recording_mode == "continuous"
        assert cam.resolution == "1080p"
        assert cam.fps == 25

    def test_create_camera_full(self, sample_camera):
        assert sample_camera.id == "cam-abc123"
        assert sample_camera.name == "Front Door"
        assert sample_camera.location == "Outdoor"
        assert sample_camera.status == "online"
        assert sample_camera.ip == "192.168.1.50"

    def test_camera_to_dict(self, sample_camera):
        d = asdict(sample_camera)
        assert d["id"] == "cam-abc123"
        assert d["name"] == "Front Door"
        assert isinstance(d, dict)

    def test_camera_default_status_is_pending(self):
        cam = Camera(id="cam-new")
        assert cam.status == "pending"

    def test_camera_optional_fields_are_none(self):
        cam = Camera(id="cam-new")
        assert cam.paired_at is None
        assert cam.last_seen is None


class TestUser:
    """Test User dataclass."""

    def test_create_user(self, sample_user):
        assert sample_user.id == "user-001"
        assert sample_user.username == "admin"
        assert sample_user.role == "admin"

    def test_default_role_is_viewer(self):
        user = User(id="u1", username="bob", password_hash="hash")
        assert user.role == "viewer"

    def test_user_to_dict(self, sample_user):
        d = asdict(sample_user)
        assert d["username"] == "admin"
        assert "password_hash" in d

    def test_last_login_defaults_none(self):
        user = User(id="u1", username="bob", password_hash="hash")
        assert user.last_login is None


class TestSettings:
    """Test Settings dataclass."""

    def test_default_settings(self):
        s = Settings()
        assert s.timezone == "Europe/Dublin"
        assert s.storage_threshold_percent == 90
        assert s.clip_duration_seconds == 180
        assert s.session_timeout_minutes == 30
        assert s.hostname == "home-monitor"
        assert s.setup_completed is False
        assert s.firmware_version == "1.0.0"

    def test_custom_settings(self):
        s = Settings(timezone="US/Eastern", storage_threshold_percent=85)
        assert s.timezone == "US/Eastern"
        assert s.storage_threshold_percent == 85

    def test_settings_to_dict(self):
        d = asdict(Settings())
        assert d["timezone"] == "Europe/Dublin"
        assert isinstance(d, dict)
        assert len(d) == 9  # 7 original + usb_device + usb_recordings_dir


class TestClip:
    """Test Clip dataclass."""

    def test_create_clip(self, sample_clip):
        assert sample_clip.camera_id == "cam-abc123"
        assert sample_clip.filename == "14-30-00.mp4"
        assert sample_clip.date == "2026-04-09"
        assert sample_clip.duration_seconds == 180

    def test_clip_defaults(self):
        clip = Clip(
            camera_id="cam-001",
            filename="10-00-00.mp4",
            date="2026-04-09",
            start_time="10:00:00",
        )
        assert clip.duration_seconds == 180
        assert clip.size_bytes == 0
        assert clip.thumbnail == ""

    def test_clip_to_dict(self, sample_clip):
        d = asdict(sample_clip)
        assert d["camera_id"] == "cam-abc123"
        assert d["size_bytes"] == 52428800
