"""
RPi Home Monitor - Server Application

Flask-based web server that manages RTSP camera streams,
records video clips, and provides a mobile-friendly web dashboard.
"""

import logging
import os
import socket
import subprocess

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from monitor.logging_config import configure_logging
from monitor.services.audit import AuditLogger
from monitor.services.camera_control_client import CameraControlClient
from monitor.services.camera_service import CameraService
from monitor.services.cert_service import CertService
from monitor.services.discovery import DiscoveryService
from monitor.services.factory_reset_service import FactoryResetService
from monitor.services.loop_recorder import LoopRecorder
from monitor.services.ota_service import OTAService
from monitor.services.pairing_service import PairingService
from monitor.services.provisioning_service import ProvisioningService
from monitor.services.recording_scheduler import RecordingScheduler
from monitor.services.recordings_service import RecordingsService
from monitor.services.settings_service import SettingsService
from monitor.services.storage_manager import StorageManager
from monitor.services.storage_service import StorageService
from monitor.services.streaming_service import StreamingService
from monitor.services.system_summary_service import SystemSummaryService
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
    if store.has_file("users.json"):
        log.warning("users.json exists but no users could be loaded; refusing to reset")
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
    # nginx sets X-Real-IP and X-Forwarded-For — trust one proxy hop
    # so request.remote_addr returns the real client IP, not 127.0.0.1
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    config_dir = os.environ.get("MONITOR_CONFIG_DIR", "/data/config")
    explicit_config_keys = set(config.keys()) if config else set()

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
        SESSION_COOKIE_SECURE=True,
    )
    if config:
        app.config.update(config)

    log.debug("Config: DATA_DIR=%s CONFIG_DIR=%s", app.config["DATA_DIR"], config_dir)

    # Load persistent secret key (unless overridden by test config)
    if not config or "SECRET_KEY" not in config:
        app.config["SECRET_KEY"] = _load_or_create_secret_key(app.config["CONFIG_DIR"])

    # --- Core infrastructure ---
    _init_infrastructure(app)

    # --- Persisted settings ---
    _load_persisted_settings(app, explicit_config_keys)

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
        threshold_percent=app.config.get("STORAGE_THRESHOLD_PERCENT"),
    )


def _load_persisted_settings(app, explicit_config_keys=None):
    """Load persisted runtime settings before service initialization."""
    explicit_config_keys = explicit_config_keys or set()
    try:
        settings = app.store.get_settings()
    except Exception as exc:
        log.warning("Failed to load persisted settings: %s", exc)
        return

    if "CLIP_DURATION_SECONDS" not in explicit_config_keys:
        app.config["CLIP_DURATION_SECONDS"] = settings.clip_duration_seconds
    if "STORAGE_THRESHOLD_PERCENT" not in explicit_config_keys:
        app.config["STORAGE_THRESHOLD_PERCENT"] = settings.storage_threshold_percent
    if "SESSION_TIMEOUT_MINUTES" not in explicit_config_keys:
        app.config["SESSION_TIMEOUT_MINUTES"] = settings.session_timeout_minutes

    app.storage_manager.set_threshold_percent(app.config["STORAGE_THRESHOLD_PERCENT"])


def _init_services(app):
    """Initialize application services with dependency injection."""
    recordings_dir = app.config["RECORDINGS_DIR"]

    app.streaming = StreamingService(
        live_dir=app.config["LIVE_DIR"],
        recordings_dir=recordings_dir,
        clip_duration=app.config.get("CLIP_DURATION_SECONDS", 180),
    )

    # Camera control client — pushes config to cameras via mTLS (ADR-0015)
    app.camera_control_client = CameraControlClient(
        certs_dir=app.config["CERTS_DIR"],
    )

    # Camera service — orchestrates store + streaming + audit
    app.camera_service = CameraService(
        store=app.store,
        streaming=app.streaming,
        audit=app.audit,
        control_client=app.camera_control_client,
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

    app.discovery_service = DiscoveryService(store=app.store, audit=app.audit)

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
    app.tailscale_service = TailscaleService(store=app.store, audit=app.audit)

    # Factory reset service — wipe all data and return to first-boot
    app.factory_reset_service = FactoryResetService(
        store=app.store,
        audit=app.audit,
        data_dir=app.config["DATA_DIR"],
    )

    # Connect storage manager → streaming service for dir change notifications
    def _on_recording_dir_change(new_dir):
        app.streaming.update_recordings_dir(new_dir)

    app.storage_manager.set_dir_change_callback(_on_recording_dir_change)

    # ADR-0017: on-demand streaming + recording-mode services
    from monitor.api.on_demand import OnDemandCoordinator

    app.on_demand_coordinator = OnDemandCoordinator(
        store=app.store,
        control_client=app.camera_control_client,
        scheduler_ref=lambda: getattr(app, "recording_scheduler", None),
    )
    app.recording_scheduler = RecordingScheduler(
        store=app.store,
        streaming=app.streaming,
        control_client=app.camera_control_client,
        coordinator=app.on_demand_coordinator,
    )
    app.loop_recorder = LoopRecorder(
        base_dir=app.config["RECORDINGS_DIR"],
        audit=app.audit,
    )

    # ADR-0018: dashboard status-strip aggregator. Pure-read, no threads, no I/O
    # in the constructor — each HTTP GET recomputes from live signals.
    from monitor.services import health as _health_module

    app.system_summary_service = SystemSummaryService(
        store=app.store,
        storage_manager=app.storage_manager,
        audit=app.audit,
        recordings_service=app.recordings_service,
        health_module=_health_module,
    )


def _restore_hostname(data_dir: str):
    """Restore hostname from /data on every boot.

    Hostname is saved during provisioning. Restoring it on startup
    ensures it survives OTA rootfs updates (even if rootfs is read-only).
    """
    hostname_file = os.path.join(data_dir, "config", "hostname")
    try:
        with open(hostname_file) as f:
            hostname = f.read().strip()
        if hostname and hostname != socket.gethostname():
            subprocess.run(
                ["hostnamectl", "set-hostname", hostname],
                capture_output=True,
                timeout=10,
            )
            log.info("Hostname restored: %s", hostname)
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("Failed to restore hostname: %s", exc)


def _startup(app):
    """Runtime startup: default admin, USB auto-mount, start services."""
    _restore_hostname(app.config.get("DATA_DIR", "/data"))
    _ensure_default_admin(app.store)
    log.debug("Default admin user ensured")

    # Auto-mount USB if previously configured
    recordings_dir = _auto_mount_usb(app, app.config["RECORDINGS_DIR"])
    if recordings_dir != app.config["RECORDINGS_DIR"]:
        app.streaming.update_recordings_dir(recordings_dir)

    app.streaming.start()
    app.storage_manager.start()
    app.cert_service.start()
    app.recording_scheduler.start()
    app.loop_recorder.start()

    # mDNS browser — discovers cameras advertising _rtsp._tcp on the LAN (RFC 6762/6763)
    app.discovery_service.start_mdns_browser()

    # Staleness checker — marks cameras offline if no heartbeat received (ADR-0016)
    _start_staleness_checker(app)

    # Resume pipelines for confirmed online cameras
    _resume_camera_pipelines(app)

    # Apply Tailscale config on boot (auto-connect if configured)
    try:
        settings = app.store.get_settings()
        if settings.tailscale_enabled:
            auth_url, err = app.tailscale_service.apply_config()
            if err:
                log.warning("Tailscale startup config failed: %s", err)
            elif auth_url:
                log.info("Tailscale needs authentication: %s", auth_url)
            else:
                log.info("Tailscale config applied on startup")
    except Exception as exc:
        log.warning("Failed to apply Tailscale config on startup: %s", exc)


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


def _start_staleness_checker(app):
    """Start background thread that marks cameras offline on missed heartbeats.

    Runs DiscoveryService.check_offline() every STALENESS_CHECK_INTERVAL seconds.
    Any camera that has not sent a heartbeat within OFFLINE_TIMEOUT (30s, defined
    in discovery.py) is moved to status='offline' and streaming=False (ADR-0016).
    """
    import threading

    staleness_check_interval = 10  # seconds

    def _run():
        import time

        while True:
            try:
                with app.app_context():
                    app.discovery_service.check_offline()
            except Exception as exc:
                log.warning("Staleness checker error: %s", exc)
            time.sleep(staleness_check_interval)

    t = threading.Thread(target=_run, name="staleness-checker", daemon=True)
    t.start()
    log.info("Staleness checker started (check every %ds)", staleness_check_interval)


def _resume_camera_pipelines(app):
    """Start streaming pipelines for cameras that were recently online.

    Skips cameras whose last_seen is stale (> OFFLINE_TIMEOUT seconds ago).
    Those cameras will be marked offline by the staleness checker and their
    pipeline will restart automatically once they reconnect via heartbeat.
    This prevents an ffmpeg crash-retry loop at boot when cameras haven't
    come up yet.
    """
    from datetime import UTC, datetime

    offline_timeout = 30  # seconds — matches discovery.py OFFLINE_TIMEOUT

    try:
        cameras = app.store.get_cameras()
        now = datetime.now(UTC)
        for cam in cameras:
            if cam.status != "online" or cam.recording_mode != "continuous":
                continue
            if cam.last_seen:
                try:
                    last = datetime.fromisoformat(cam.last_seen.replace("Z", "+00:00"))
                    elapsed = (now - last).total_seconds()
                    if elapsed > offline_timeout:
                        log.info(
                            "Skipping pipeline start for %s (last seen %ds ago, stale)",
                            cam.id,
                            int(elapsed),
                        )
                        continue
                except (ValueError, TypeError):
                    pass
            app.streaming.start_camera(cam.id)
    except Exception:
        pass  # Don't crash on startup if cameras.json has issues


def _register_blueprints(app):
    """Register all Flask blueprints."""
    from monitor.api.audit import audit_bp
    from monitor.api.cameras import cameras_bp
    from monitor.api.live import live_bp
    from monitor.api.on_demand import on_demand_bp
    from monitor.api.ota import ota_bp
    from monitor.api.pairing import pairing_bp
    from monitor.api.recordings import recordings_bp
    from monitor.api.settings import settings_bp
    from monitor.api.storage import storage_bp
    from monitor.api.system import system_bp
    from monitor.api.users import users_bp
    from monitor.api.webrtc import webrtc_bp
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
    app.register_blueprint(webrtc_bp, url_prefix="/api/v1/webrtc")
    app.register_blueprint(audit_bp, url_prefix="/api/v1/audit")
    # ADR-0017: localhost-only on-demand coordinator for MediaMTX hooks.
    # Mounted outside /api/v1 so the CSRF layer on /api/* can stay strict.
    app.register_blueprint(on_demand_bp, url_prefix="/internal/on-demand")
