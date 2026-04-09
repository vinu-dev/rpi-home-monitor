"""
RPi Home Monitor - Server Application

Flask-based web server that manages RTSP camera streams,
records video clips, and provides a mobile-friendly web dashboard.
"""
import os
from flask import Flask

from monitor.services.audit import AuditLogger
from monitor.store import Store


def _load_or_create_secret_key(config_dir):
    """Load persistent secret key, or create one on first boot."""
    key_file = os.path.join(config_dir, ".secret_key")
    try:
        with open(key_file, "r") as f:
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

    from monitor.auth import hash_password
    from monitor.models import User
    from datetime import datetime, timezone

    admin = User(
        id="user-admin-default",
        username="admin",
        password_hash=hash_password("admin"),
        role="admin",
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    store.save_user(admin)


def create_app(config=None):
    """Application factory.

    Creates and configures the Flask application with all blueprints,
    background services, and security middleware.
    """
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
        SESSION_TIMEOUT_MINUTES=30,
    )

    if config:
        app.config.update(config)

    # Load persistent secret key (unless overridden by test config)
    if not config or "SECRET_KEY" not in config:
        app.config["SECRET_KEY"] = _load_or_create_secret_key(app.config["CONFIG_DIR"])

    # Initialize data store and audit logger
    app.store = Store(app.config["CONFIG_DIR"])
    logs_dir = os.path.join(app.config["DATA_DIR"], "logs")
    app.audit = AuditLogger(logs_dir)

    # Create default admin user on first boot (skip in test mode)
    if not app.config.get("TESTING"):
        _ensure_default_admin(app.store)

    # Register blueprints
    from monitor.api.cameras import cameras_bp
    from monitor.api.recordings import recordings_bp
    from monitor.api.live import live_bp
    from monitor.api.system import system_bp
    from monitor.api.settings import settings_bp
    from monitor.api.users import users_bp
    from monitor.api.ota import ota_bp
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

    return app
