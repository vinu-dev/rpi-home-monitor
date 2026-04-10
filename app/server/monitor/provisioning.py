"""
WiFi hotspot provisioning module.

Provides a Flask blueprint for the first-boot setup wizard. When the RPi 4B
boots without WiFi configured, it creates a hotspot so the user can connect
from their phone and configure WiFi credentials + admin password.

All setup endpoints require that /data/.setup-done does NOT exist. Once setup
is complete, the stamp file is written and all endpoints return 403.
"""
import logging
import os
import subprocess

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
)

log = logging.getLogger("monitor.provisioning")

provisioning_bp = Blueprint("provisioning", __name__)

HOTSPOT_SCRIPT = "/opt/monitor/scripts/monitor-hotspot.sh"


def _setup_done_path():
    """Get the path to the setup-done stamp file."""
    data_dir = current_app.config.get("DATA_DIR", "/data")
    return os.path.join(data_dir, ".setup-done")


def _is_setup_complete():
    """Check whether initial setup has already been completed."""
    path = _setup_done_path()
    done = os.path.exists(path)
    log.debug("Setup complete check: %s exists=%s", path, done)
    return done


def _require_setup_mode():
    """Return a 403 response if setup is already complete, or None to proceed."""
    if _is_setup_complete():
        return jsonify({"error": "Setup already completed"}), 403
    return None


def _is_hotspot_active():
    """Check if the setup hotspot is currently active."""
    try:
        result = subprocess.run(
            [HOTSPOT_SCRIPT, "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


@provisioning_bp.route("/status", methods=["GET"])
def setup_status():
    """Return current setup state.

    No authentication required. Used by the setup wizard and the main
    dashboard to determine which mode to show.
    """
    complete = _is_setup_complete()
    hotspot = _is_hotspot_active()
    log.debug("GET /status — setup_complete=%s hotspot_active=%s", complete, hotspot)
    return jsonify({
        "setup_complete": complete,
        "hotspot_active": hotspot,
    }), 200


@provisioning_bp.route("/wifi/scan", methods=["GET"])
def wifi_scan():
    """Scan for available WiFi networks.

    Returns a deduplicated list sorted by signal strength (strongest first).
    Only available during setup mode.
    """
    blocked = _require_setup_mode()
    if blocked:
        return blocked

    log.info("WiFi scan requested")
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
             "--rescan", "yes"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        log.error("WiFi scan timed out")
        return jsonify({"error": "WiFi scan timed out"}), 504
    except (FileNotFoundError, OSError) as exc:
        log.error("WiFi scan failed: %s", exc)
        return jsonify({"error": f"WiFi scan failed: {exc}"}), 500

    if result.returncode != 0:
        log.error("WiFi scan nmcli error: %s", result.stderr.strip())
        return jsonify({"error": "WiFi scan failed", "detail": result.stderr.strip()}), 500

    # Parse nmcli output: "SSID:SIGNAL:SECURITY"
    networks = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid = parts[0].strip()
        if not ssid:
            continue
        try:
            signal = int(parts[1].strip())
        except (ValueError, IndexError):
            signal = 0
        security = parts[2].strip() if len(parts) >= 3 else ""

        # Keep the strongest signal per SSID
        if ssid not in networks or signal > networks[ssid]["signal"]:
            networks[ssid] = {
                "ssid": ssid,
                "signal": signal,
                "security": security,
            }

    # Sort by signal strength descending
    network_list = sorted(networks.values(), key=lambda n: n["signal"], reverse=True)

    log.info("WiFi scan found %d networks", len(network_list))
    log.debug("Networks: %s", [n["ssid"] for n in network_list])
    return jsonify({"networks": network_list}), 200


@provisioning_bp.route("/wifi/connect", methods=["POST"])
def wifi_connect():
    """Connect to a WiFi network.

    Expects JSON body: {"ssid": "...", "password": "..."}
    Only available during setup mode.
    """
    blocked = _require_setup_mode()
    if blocked:
        return blocked

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()

    if not ssid:
        return jsonify({"error": "SSID is required"}), 400
    if not password:
        return jsonify({"error": "Password is required"}), 400

    log.info("WiFi connect requested: SSID=%s", ssid)
    try:
        result = subprocess.run(
            ["nmcli", "dev", "wifi", "connect", ssid,
             "password", password, "ifname", "wlan0"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        log.error("WiFi connect timed out for SSID=%s", ssid)
        return jsonify({"error": "WiFi connection timed out"}), 504
    except (FileNotFoundError, OSError) as exc:
        log.error("WiFi connect failed for SSID=%s: %s", ssid, exc)
        return jsonify({"error": f"WiFi connection failed: {exc}"}), 500

    if result.returncode != 0:
        stderr = result.stderr.strip()
        log.error("WiFi connect failed for SSID=%s: %s", ssid, stderr)
        # Common failure: wrong password
        if "secrets were required" in stderr.lower() or "no suitable" in stderr.lower():
            return jsonify({"error": "Incorrect WiFi password"}), 401
        return jsonify({"error": "WiFi connection failed", "detail": stderr}), 500

    log.info("WiFi connected to %s", ssid)
    return jsonify({"message": f"Connected to {ssid}"}), 200


@provisioning_bp.route("/admin", methods=["POST"])
def set_admin_password():
    """Set a new admin password.

    Expects JSON body: {"password": "..."} (minimum 8 characters).
    Updates the default admin user's password hash.
    Only available during setup mode.
    """
    blocked = _require_setup_mode()
    if blocked:
        return blocked

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    password = data.get("password", "")
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    store = current_app.store
    admin = store.get_user_by_username("admin")
    if not admin:
        return jsonify({"error": "Default admin user not found"}), 500

    from monitor.auth import hash_password
    admin.password_hash = hash_password(password)
    store.save_user(admin)

    return jsonify({"message": "Admin password updated"}), 200


@provisioning_bp.route("/complete", methods=["POST"])
def setup_complete():
    """Mark initial setup as complete.

    Writes the /data/.setup-done stamp file, stops the hotspot,
    and returns the new WiFi IP address for the user to reconnect.
    """
    blocked = _require_setup_mode()
    if blocked:
        return blocked

    log.info("Marking setup as complete")

    # Write the setup-done stamp file
    stamp = _setup_done_path()
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write("setup completed\n")
        log.info("Stamp file written: %s", stamp)
    except OSError as exc:
        log.error("Failed to write stamp file %s: %s", stamp, exc)
        return jsonify({"error": f"Failed to mark setup complete: {exc}"}), 500

    # Stop the hotspot
    log.info("Stopping hotspot...")
    try:
        result = subprocess.run(
            [HOTSPOT_SCRIPT, "stop"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        log.debug("Hotspot stop: rc=%d stdout=%s", result.returncode, result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.warning("Hotspot stop failed (non-fatal): %s", exc)

    # Get the new WiFi IP address
    ip_address = ""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "IP4.ADDRESS", "dev", "show", "wlan0"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                if ":" in line:
                    addr = line.split(":", 1)[1].strip()
                    # Remove CIDR suffix (e.g., /24)
                    if "/" in addr:
                        addr = addr.split("/")[0]
                    if addr:
                        ip_address = addr
                        break
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    log.info("Setup complete! New WiFi IP: %s", ip_address or "unknown")
    return jsonify({
        "message": "Setup complete",
        "ip": ip_address,
    }), 200


@provisioning_bp.route("/wizard", methods=["GET"])
def setup_wizard():
    """Serve the setup wizard HTML page."""
    log.debug("Serving setup wizard")
    return render_template("setup.html")
