"""
RPi Home Monitor - Server Application

Flask-based web server that manages RTSP camera streams,
records video clips, and provides a mobile-friendly web dashboard.
"""
import os
from flask import Flask


def create_app(config=None):
    """Application factory.

    Creates and configures the Flask application with all blueprints,
    background services, and security middleware.
    """
    app = Flask(__name__)

    # Default config
    app.config.update(
        SECRET_KEY=os.urandom(32).hex(),
        DATA_DIR=os.environ.get("MONITOR_DATA_DIR", "/data"),
        RECORDINGS_DIR=os.environ.get("MONITOR_RECORDINGS_DIR", "/data/recordings"),
        LIVE_DIR=os.environ.get("MONITOR_LIVE_DIR", "/data/live"),
        CONFIG_DIR=os.environ.get("MONITOR_CONFIG_DIR", "/data/config"),
        CERTS_DIR=os.environ.get("MONITOR_CERTS_DIR", "/data/certs"),
        CLIP_DURATION_SECONDS=180,
        STORAGE_THRESHOLD_PERCENT=90,
        SESSION_TIMEOUT_MINUTES=30,
    )

    if config:
        app.config.update(config)

    # Register blueprints
    from monitor.api.cameras import cameras_bp
    from monitor.api.recordings import recordings_bp
    from monitor.api.live import live_bp
    from monitor.api.system import system_bp
    from monitor.api.settings import settings_bp
    from monitor.api.users import users_bp
    from monitor.api.ota import ota_bp
    from monitor.auth import auth_bp

    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(cameras_bp, url_prefix="/api/v1/cameras")
    app.register_blueprint(recordings_bp, url_prefix="/api/v1/recordings")
    app.register_blueprint(live_bp, url_prefix="/api/v1/live")
    app.register_blueprint(system_bp, url_prefix="/api/v1/system")
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    app.register_blueprint(users_bp, url_prefix="/api/v1/users")
    app.register_blueprint(ota_bp, url_prefix="/api/v1/ota")

    # Start background services
    # TODO: Initialize RecorderService, DiscoveryService, StorageManager,
    #       HealthMonitor, AuditLogger as background threads

    return app
