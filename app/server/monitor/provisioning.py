"""
WiFi hotspot provisioning blueprint — thin HTTP adapter.

All business logic delegated to ProvisioningService.
Routes handle HTTP parsing and return JSON responses.

Security notes:
- Sensitive endpoints (wifi/save, admin, complete) are blocked once setup is done.
  This prevents a LAN attacker from calling /setup/admin to take over the device
  after the owner has completed initial setup.
- Rate limiting (5 requests per IP per 60s) prevents automated probing during
  the brief first-boot window when setup is incomplete.
"""

import functools
import logging
import time
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
)

log = logging.getLogger("monitor.provisioning")

provisioning_bp = Blueprint("provisioning", __name__)

# ── Setup endpoint rate limiter ────────────────────────────────────────────────
# Simple in-memory limiter to prevent automated probing during the setup window.
# State is stored per Flask app instance (on the app object) so each test app
# gets its own fresh counters — prevents test state bleed.
# Separate from the login rate limiter in auth.py.
_SETUP_RATE_WINDOW = 60   # seconds
_SETUP_RATE_MAX = 5       # max attempts per window per IP


def _get_setup_attempts() -> dict:
    """Return the per-app rate limit state, creating it if needed.

    Stored on the app object so each Flask test app gets its own fresh
    rate limit counter — prevents state from bleeding across tests.
    """
    app_obj = current_app._get_current_object()
    if not hasattr(app_obj, "_setup_rate_attempts"):
        app_obj._setup_rate_attempts = {}
    return app_obj._setup_rate_attempts


def _setup_rate_limited(ip: str) -> bool:
    """Return True if the IP has exceeded the setup rate limit."""
    now = time.time()
    store = _get_setup_attempts()
    attempts = [t for t in store.get(ip, []) if now - t < _SETUP_RATE_WINDOW]
    store[ip] = attempts
    if len(attempts) >= _SETUP_RATE_MAX:
        return True
    attempts.append(now)
    store[ip] = attempts
    return False


def _require_setup_incomplete(f):
    """Decorator: block the endpoint if setup has already been completed.

    Prevents LAN attackers from calling /setup/admin or /setup/wifi/save
    on a device that is already provisioned.
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if current_app.provisioning_service.is_setup_complete():
            return jsonify({"error": "Setup already complete"}), 403
        ip = request.remote_addr or ""
        if _setup_rate_limited(ip):
            return jsonify({"error": "Too many requests, please wait"}), 429
        return f(*args, **kwargs)
    return decorated


@provisioning_bp.route("/status", methods=["GET"])
def setup_status():
    """Return current setup state."""
    result = current_app.provisioning_service.get_status()
    return jsonify(result), 200


@provisioning_bp.route("/wifi/scan", methods=["GET"])
@_require_setup_incomplete
def wifi_scan():
    """Scan for available WiFi networks."""
    networks, err, status = current_app.provisioning_service.scan_wifi()
    if err:
        return jsonify({"error": err}), status
    return jsonify({"networks": networks}), 200


@provisioning_bp.route("/wifi/save", methods=["POST"])
@_require_setup_incomplete
def wifi_save():
    """Save WiFi credentials for later use."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    msg, status = current_app.provisioning_service.save_wifi_credentials(
        ssid=data.get("ssid", ""),
        password=data.get("password", ""),
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status


@provisioning_bp.route("/admin", methods=["POST"])
@_require_setup_incomplete
def set_admin_password():
    """Set a new admin password."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    msg, status = current_app.provisioning_service.set_admin_password(
        password=data.get("password", ""),
    )
    if status != 200:
        return jsonify({"error": msg}), status
    return jsonify({"message": msg}), status


@provisioning_bp.route("/complete", methods=["POST"])
@_require_setup_incomplete
def setup_complete():
    """Apply all settings and finish setup."""
    result, err, status = current_app.provisioning_service.complete_setup()
    if err:
        return jsonify({"error": err}), status
    return jsonify(result), status


@provisioning_bp.route("/ca-cert", methods=["GET"])
def get_ca_cert():
    """Serve the server CA certificate for camera trust-on-first-use (TOFU) verification.

    No authentication required — the CA cert is public information.
    Cameras fetch this before PIN exchange so they can verify the server's
    TLS certificate, preventing passive MITM during the pairing bootstrap
    (ADR-0009, TOFU pattern — RFC 8555 ACME §10.2).

    Available on both HTTP and HTTPS so cameras can reach it before
    they have a verified cert to use.
    """
    certs_dir = current_app.config.get("CERTS_DIR", "/data/certs")
    ca_cert_path = Path(certs_dir) / "ca.crt"
    if not ca_cert_path.is_file():
        return jsonify({"error": "CA certificate not available"}), 404
    return send_file(
        str(ca_cert_path),
        mimetype="application/x-pem-file",
        as_attachment=False,
    )


@provisioning_bp.route("/wizard", methods=["GET"])
def setup_wizard():
    """Serve the setup wizard HTML page."""
    from monitor.services.provisioning_service import SERVER_HOSTNAME

    return render_template("setup.html", hostname=f"{SERVER_HOSTNAME}.local")
