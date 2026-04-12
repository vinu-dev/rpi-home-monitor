"""
RPi Home Monitor - Server Application

Flask-based web server that manages RTSP camera streams,
records video clips, and provides a mobile-friendly web dashboard.
"""

import logging
import os

from flask import Flask

from monitor.logging_config import configure_logging
from monitor.services.audit import AuditLogger
from monitor.services.camera_service import CameraService
from monitor.services.cert_service import CertService
from monitor.services.ota_service import OTAService
from monitor.services.pairing_service import PairingService
from monitor.services.provisioning_service import ProvisioningService
from monitor.services.recordings_service import RecordingsService
from monitor.services.settings_service import SettingsService
from monitor.services.storage_manager import StorageManager
from monitor.services.storage_service import StorageService
from monitor.services.streaming_service import StreamingService
from monitor.services.tailscale_service import TailscaleService
from monitor.services.user_service import UserService
from monitor.store import Store

log = logging.getLogger("monitor")


def _load_or_create_secret_key(config_dir):
    """Load persistent secret key, or create one on first boot."""
    key_file = os.path.join(config_dir, ".secret_key")
    try:
        with open(key_file) as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass

    key = os.urandom(32).hex()
    try:
        os.makedirs(config_dir, exist_ok=True)
        with open(key_file, "w") as f:
            f.write(key)
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return key


def _ensure_default_admin(store):
    """Create a default admin user if no users exist."""
    users = store.get_users()
    if users:
        return

    from datetime import UTC, datetime

    from monitor.auth import hash_password
    from monitor.models import User

    admin = User(
        id="user-admin-default",
        username="admin",
        password_hash=hash_password("admin"),
        role="admin",
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        must_change_password=True,
    )
    store.save_user(admin)


def create_app(config=None):
    """Application factory.

    Creates and configures the Flask application with all blueprints,
    background services, and security middleware.
    """
    configure_logging()
    log.info("Monitor server starting")

    app = Flask(__name__)
    config_dir = os.environ.get("MONITOR_CONFIG_DIR", "/data/config")

    # Default config
    app.config.update(
        SECRET_KEY=os.urandom(32).hex(),
        DATA_DIR=os.environ.get("MONITOR_DATA_DIR", "/data"),
        RECORDINGS_DIR=os.environ.get("MONITOR_RECORDINGS_DIR", "/data/recordings"),
        LIVE_DIR=os.environ.get("MONITOR_LIVE_DIR", "/data/live"),
        CONFIG_DIR=config_dir,
        CERTS_DIR=os.environ.get("MONITOR_CERTS_DIR", "/data/certs"),
        CLIP_DURATION_SECONDS=180,
        STORAGE_THRESHOLD_PERCENT=90,
        SESSION_TIMEOUT_MINUTES=60,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
    )
    if config:
        app.config.update(config)

    log.debug("Config: DATA_DIR=%s CONFIG_DIR=%s", app.config["DATA_DIR"], config_dir)

    # Load persistent secret key (unless overridden by test config)
    if not config or "SECRET_KEY" not in config:
        app.config["SECRET_KEY"] = _load_or_create_secret_key(app.config["CONFIG_DIR"])

    # --- Core infrastructure ---
    _init_infrastructure(app)

    # --- Application services ---
    _init_services(app)

    # --- Runtime startup (skip in tests) ---
    if not app.config.get("TESTING"):
        _startup(app)

    # --- Register blueprints ---
    _register_blueprints(app)

    log.info("Monitor server ready — blueprints registered")
    return app


def _init_infrastructure(app):
    """Initialize core infrastructure: store, audit, storage manager."""
    log.debug("Initializing data store at %s", app.config["CONFIG_DIR"])
    app.store = Store(app.config["CONFIG_DIR"])

    logs_dir = os.path.join(app.config["DATA_DIR"], "logs")
    app.audit = AuditLogger(logs_dir)

    app.storage_manager = StorageManager(
        recordings_dir=app.config["RECORDINGS_DIR"],
        data_dir=app.config["DATA_DIR"],
    )


def _init_services(app):
    """Initialize application services with dependency injection."""
    recordings_dir = app.config["RECORDINGS_DIR"]

    app.streaming = StreamingService(
        live_dir=app.config["LIVE_DIR"],
        recordings_dir=recordings_dir,
        clip_duration=app.config.get("CLIP_DURATION_SECONDS", 180),
    )

    # Camera service — orchestrates store + streaming + audit
    app.camera_service = CameraService(
        store=app.store,
        streaming=app.streaming,
        audit=app.audit,
    )

    # Storage service — orchestrates USB + storage manager + store + audit
    app.storage_service = StorageService(
        storage_manager=app.storage_manager,
        store=app.store,
        audit=app.audit,
        default_recordings_dir=app.config["RECORDINGS_DIR"],
    )

    # User service — user CRUD + password management
    app.user_service = UserService(store=app.store, audit=app.audit)

    # Recordings service — clip queries, deletion, audit
    app.recordings_service = RecordingsService(
        storage_manager=app.storage_manager,
        store=app.store,
        audit=app.audit,
        live_dir=app.config["LIVE_DIR"],
        default_recordings_dir=recordings_dir,
    )

    # Pairing service — camera cert exchange and revocation
    app.pairing_service = PairingService(
        store=app.store,
        audit=app.audit,
        certs_dir=app.config["CERTS_DIR"],
    )

    # Settings service — system config + WiFi management
    app.settings_service = SettingsService(store=app.store, audit=app.audit)

    # Provisioning service — first-boot setup wizard
    app.provisioning_service = ProvisioningService(
        store=app.store,
        data_dir=app.config["DATA_DIR"],
    )

    # OTA service — bundle staging, verification, installation
    app.ota_service = OTAService(
        store=app.store,
        audit=app.audit,
        data_dir=app.config["DATA_DIR"],
    )

    # Certificate service — expiry monitoring and renewal
    app.cert_service = CertService(
        certs_dir=app.config["CERTS_DIR"],
        audit=app.audit,
    )

    # Tailscale service — VPN status and management
    app.tailscale_service = TailscaleService(audit=app.audit)

    # Connect storage manager → streaming service for dir change notifications
    def _on_recording_dir_change(new_dir):
        app.streaming.update_recordings_dir(new_dir)

    app.storage_manager.set_dir_change_callback(_on_recording_dir_change)


def _startup(app):
    """Runtime startup: default admin, USB auto-mount, start services."""
    _ensure_default_admin(app.store)
    log.debug("Default admin user ensured")

    # Auto-mount USB if previously configured
    recordings_dir = _auto_mount_usb(app, app.config["RECORDINGS_DIR"])
    if recordings_dir != app.config["RECORDINGS_DIR"]:
        app.streaming.update_recordings_dir(recordings_dir)

    app.streaming.start()
    app.storage_manager.start()
    app.cert_service.start()

    # Resume pipelines for confirmed online cameras
    _resume_camera_pipelines(app)


def _auto_mount_usb(app, default_recordings_dir):
    """Auto-mount USB if previously configured in settings.

    Returns the recordings directory to use (USB or default).
    """
    try:
        settings = app.store.get_settings()
        usb_device = getattr(settings, "usb_device", "")
        usb_rec_dir = getattr(settings, "usb_recordings_dir", "")

        if not usb_device or not usb_rec_dir:
            return default_recordings_dir

        from monitor.services import usb

        devices = usb.detect_devices()
        found = any(d["path"] == usb_device for d in devices)
        if not found:
            log.warning(
                "Configured USB device %s not found — using internal storage",
                usb_device,
            )
            return default_recordings_dir

        ok, err = usb.mount_device(usb_device)
        if not ok:
            log.error(
                "Failed to auto-mount USB %s: %s — using internal storage",
                usb_device,
                err,
            )
            return default_recordings_dir

        rec_dir = usb.prepare_recordings_dir()
        app.storage_manager.set_recordings_dir(rec_dir)
        log.info("Auto-mounted USB storage: %s -> %s", usb_device, rec_dir)
        return rec_dir

    except Exception as e:
        log.error("USB auto-mount failed: %s", e)
        return default_recordings_dir


def _resume_camera_pipelines(app):
    """Start streaming pipelines for cameras that were online before restart."""
    try:
        cameras = app.store.get_cameras()
        for cam in cameras:
            if cam.status == "online" and cam.recording_mode == "continuous":
                app.streaming.start_camera(cam.id)
    except Exception:
        pass  # Don't crash on startup if cameras.json has issues


def _register_blueprints(app):
    """Register all Flask blueprints."""
    from monitor.api.cameras import cameras_bp
    from monitor.api.live import live_bp
    from monitor.api.ota import ota_bp
    from monitor.api.pairing import pairing_bp
    from monitor.api.recordings import recordings_bp
    from monitor.api.settings import settings_bp
    from monitor.api.storage import storage_bp
    from monitor.api.system import system_bp
    from monitor.api.users import users_bp
    from monitor.auth import auth_bp
    from monitor.provisioning import provisioning_bp as setup_bp
    from monitor.views import views_bp

    app.register_blueprint(views_bp)
    app.register_blueprint(setup_bp, url_prefix="/api/v1/setup")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(cameras_bp, url_prefix="/api/v1/cameras")
    app.register_blueprint(recordings_bp, url_prefix="/api/v1/recordings")
    app.register_blueprint(live_bp, url_prefix="/api/v1/live")
    app.register_blueprint(system_bp, url_prefix="/api/v1/system")
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    app.register_blueprint(users_bp, url_prefix="/api/v1/users")
    app.register_blueprint(ota_bp, url_prefix="/api/v1/ota")
    app.register_blueprint(pairing_bp, url_prefix="/api/v1")
    app.register_blueprint(storage_bp, url_prefix="/api/v1/storage")
