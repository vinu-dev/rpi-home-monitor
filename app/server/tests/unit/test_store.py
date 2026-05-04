# REQ: SWR-021, SWR-023, SWR-024, SWR-025, SWR-066; RISK: RISK-010, RISK-011, RISK-012, RISK-015; SEC: SC-010, SC-011, SC-012; TEST: TC-021, TC-022, TC-023, TC-030, TC-054
"""Tests for the JSON persistence layer."""

import json
import threading

from monitor.models import Camera, Settings, User, WebhookDestination
from monitor.store import Store


class TestStoreInit:
    """Test Store initialization."""

    def test_creates_config_dir(self, tmp_path):
        config_dir = tmp_path / "newdir"
        Store(str(config_dir))
        assert config_dir.exists()

    def test_works_with_existing_dir(self, data_dir):
        store = Store(str(data_dir / "config"))
        assert store.config_dir.exists()


class TestCameraStore:
    """Test camera CRUD operations."""

    def test_get_cameras_empty(self, data_dir):
        store = Store(str(data_dir / "config"))
        assert store.get_cameras() == []

    def test_save_and_get_camera(self, data_dir, sample_camera):
        store = Store(str(data_dir / "config"))
        store.save_camera(sample_camera)
        cameras = store.get_cameras()
        assert len(cameras) == 1
        assert cameras[0].id == sample_camera.id
        assert cameras[0].name == sample_camera.name

    def test_camera_round_trips_time_health_fields(self, data_dir):
        store = Store(str(data_dir / "config"))
        camera = Camera(
            id="cam-001",
            last_beat_camera_ts="2026-05-04T12:00:00Z",
            pending_config={"time_resync": True},
        )

        store.save_camera(camera)
        loaded = store.get_camera("cam-001")

        assert loaded is not None
        assert loaded.last_beat_camera_ts == "2026-05-04T12:00:00Z"
        assert loaded.pending_config == {"time_resync": True}

    def test_camera_round_trips_encoder_preset(self, data_dir):
        store = Store(str(data_dir / "config"))
        camera = Camera(id="cam-001", encoder_preset="balanced")

        store.save_camera(camera)
        loaded = store.get_camera("cam-001")

        assert loaded is not None
        assert loaded.encoder_preset == "balanced"

    def test_get_camera_by_id(self, data_dir, sample_camera):
        store = Store(str(data_dir / "config"))
        store.save_camera(sample_camera)
        cam = store.get_camera(sample_camera.id)
        assert cam is not None
        assert cam.id == sample_camera.id

    def test_get_camera_not_found(self, data_dir):
        store = Store(str(data_dir / "config"))
        assert store.get_camera("nonexistent") is None

    def test_update_camera(self, data_dir, sample_camera):
        store = Store(str(data_dir / "config"))
        store.save_camera(sample_camera)
        sample_camera.name = "Back Yard"
        store.save_camera(sample_camera)
        cameras = store.get_cameras()
        assert len(cameras) == 1
        assert cameras[0].name == "Back Yard"

    def test_save_multiple_cameras(self, data_dir):
        store = Store(str(data_dir / "config"))
        store.save_camera(Camera(id="cam-001", name="Front"))
        store.save_camera(Camera(id="cam-002", name="Back"))
        cameras = store.get_cameras()
        assert len(cameras) == 2

    def test_delete_camera(self, data_dir, sample_camera):
        store = Store(str(data_dir / "config"))
        store.save_camera(sample_camera)
        result = store.delete_camera(sample_camera.id)
        assert result is True
        assert store.get_cameras() == []

    def test_delete_camera_not_found(self, data_dir):
        store = Store(str(data_dir / "config"))
        result = store.delete_camera("nonexistent")
        assert result is False

    def test_delete_preserves_other_cameras(self, data_dir):
        store = Store(str(data_dir / "config"))
        store.save_camera(Camera(id="cam-001", name="Front"))
        store.save_camera(Camera(id="cam-002", name="Back"))
        store.delete_camera("cam-001")
        cameras = store.get_cameras()
        assert len(cameras) == 1
        assert cameras[0].id == "cam-002"

    def test_handles_corrupt_json(self, data_dir):
        store = Store(str(data_dir / "config"))
        (data_dir / "config" / "cameras.json").write_text("not json{{{")
        assert store.get_cameras() == []

    def test_handles_wrong_type_in_json(self, data_dir):
        store = Store(str(data_dir / "config"))
        (data_dir / "config" / "cameras.json").write_text('"just a string"')
        assert store.get_cameras() == []


class TestUserStore:
    """Test user CRUD operations."""

    def test_get_users_empty(self, data_dir):
        store = Store(str(data_dir / "config"))
        assert store.get_users() == []

    def test_save_and_get_user(self, data_dir, sample_user):
        store = Store(str(data_dir / "config"))
        store.save_user(sample_user)
        users = store.get_users()
        assert len(users) == 1
        assert users[0].username == "admin"

    def test_get_user_by_id(self, data_dir, sample_user):
        store = Store(str(data_dir / "config"))
        store.save_user(sample_user)
        user = store.get_user(sample_user.id)
        assert user is not None
        assert user.username == "admin"

    def test_get_user_by_username(self, data_dir, sample_user):
        store = Store(str(data_dir / "config"))
        store.save_user(sample_user)
        user = store.get_user_by_username("admin")
        assert user is not None
        assert user.id == sample_user.id

    def test_get_user_by_username_not_found(self, data_dir):
        store = Store(str(data_dir / "config"))
        assert store.get_user_by_username("nobody") is None

    def test_get_user_not_found(self, data_dir):
        store = Store(str(data_dir / "config"))
        assert store.get_user("nonexistent") is None

    def test_update_user(self, data_dir, sample_user):
        store = Store(str(data_dir / "config"))
        store.save_user(sample_user)
        sample_user.role = "viewer"
        sample_user.notification_schedule = [
            {"days": ["mon"], "start": "22:00", "end": "06:00"}
        ]
        store.save_user(sample_user)
        users = store.get_users()
        assert len(users) == 1
        assert users[0].role == "viewer"
        assert users[0].notification_schedule[0]["start"] == "22:00"

    def test_delete_user(self, data_dir, sample_user):
        store = Store(str(data_dir / "config"))
        store.save_user(sample_user)
        result = store.delete_user(sample_user.id)
        assert result is True
        assert store.get_users() == []

    def test_delete_user_not_found(self, data_dir):
        store = Store(str(data_dir / "config"))
        result = store.delete_user("nonexistent")
        assert result is False

    def test_handles_corrupt_json(self, data_dir):
        store = Store(str(data_dir / "config"))
        (data_dir / "config" / "users.json").write_text("{broken")
        assert store.get_users() == []


class TestSettingsStore:
    """Test settings read/write."""

    def test_get_default_settings(self, data_dir):
        store = Store(str(data_dir / "config"))
        settings = store.get_settings()
        assert settings.timezone == "Europe/Dublin"
        assert settings.storage_threshold_percent == 90
        assert settings.setup_completed is False

    def test_save_and_get_settings(self, data_dir):
        store = Store(str(data_dir / "config"))
        settings = Settings(timezone="US/Eastern", setup_completed=True)
        store.save_settings(settings)
        loaded = store.get_settings()
        assert loaded.timezone == "US/Eastern"
        assert loaded.setup_completed is True

    def test_update_settings(self, data_dir):
        store = Store(str(data_dir / "config"))
        store.save_settings(Settings(timezone="US/Eastern"))
        store.save_settings(Settings(timezone="Asia/Tokyo"))
        loaded = store.get_settings()
        assert loaded.timezone == "Asia/Tokyo"

    def test_handles_corrupt_json(self, data_dir):
        store = Store(str(data_dir / "config"))
        (data_dir / "config" / "settings.json").write_text("xxx")
        settings = store.get_settings()
        assert settings.timezone == "Europe/Dublin"  # Returns defaults

    def test_handles_empty_json(self, data_dir):
        store = Store(str(data_dir / "config"))
        (data_dir / "config" / "settings.json").write_text("{}")
        settings = store.get_settings()
        # Empty dict creates Settings with all defaults
        assert settings.timezone == "Europe/Dublin"

    def test_round_trips_webhook_destinations(self, data_dir):
        store = Store(str(data_dir / "config"))
        settings = Settings(
            webhook_destinations=[
                WebhookDestination(
                    id="wh-123",
                    url="https://hooks.example.com/inbound",
                    auth_type="hmac",
                    secret="super-secret",
                    custom_headers={"X-Env": "test"},
                    event_classes=("motion", "storage_low"),
                )
            ]
        )
        store.save_settings(settings)

        loaded = store.get_settings()
        assert len(loaded.webhook_destinations) == 1
        assert loaded.webhook_destinations[0].id == "wh-123"
        assert loaded.webhook_destinations[0].event_classes == (
            "motion",
            "storage_low",
        )

    def test_round_trips_offsite_backup_fields(self, data_dir):
        store = Store(str(data_dir / "config"))
        settings = Settings(
            offsite_backup_enabled=True,
            offsite_backup_endpoint="minio.example.com:9000",
            offsite_backup_bucket="hm-backups",
            offsite_backup_access_key_id="AKIATEST",
            offsite_backup_secret_access_key="secret-value",
            offsite_backup_prefix="backups/home-monitor",
            offsite_backup_retention_days=90,
            offsite_backup_bandwidth_cap_mbps=12.5,
        )
        store.save_settings(settings)

        loaded = store.get_settings()
        assert loaded.offsite_backup_enabled is True
        assert loaded.offsite_backup_bucket == "hm-backups"
        assert loaded.offsite_backup_secret_access_key == "secret-value"
        assert loaded.offsite_backup_bandwidth_cap_mbps == 12.5


class TestAtomicWrite:
    """Test that writes are atomic (no .tmp files left behind)."""

    def test_no_tmp_files_after_write(self, data_dir):
        store = Store(str(data_dir / "config"))
        store.save_camera(Camera(id="cam-001"))
        tmp_files = list((data_dir / "config").glob("*.tmp"))
        assert tmp_files == []

    def test_json_is_valid_after_write(self, data_dir):
        store = Store(str(data_dir / "config"))
        store.save_camera(Camera(id="cam-001", name="Test"))
        raw = json.loads((data_dir / "config" / "cameras.json").read_text())
        assert isinstance(raw, list)
        assert raw[0]["id"] == "cam-001"


class TestThreadSafety:
    """Test concurrent access doesn't corrupt data."""

    def test_concurrent_camera_writes(self, data_dir):
        store = Store(str(data_dir / "config"))
        errors = []

        def write_camera(i):
            try:
                store.save_camera(Camera(id=f"cam-{i:03d}", name=f"Camera {i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_camera, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        cameras = store.get_cameras()
        assert len(cameras) == 20

    def test_concurrent_user_writes(self, data_dir):
        store = Store(str(data_dir / "config"))
        errors = []

        def write_user(i):
            try:
                store.save_user(
                    User(
                        id=f"user-{i:03d}",
                        username=f"user{i}",
                        password_hash=f"hash{i}",
                    )
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_user, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        users = store.get_users()
        assert len(users) == 20
