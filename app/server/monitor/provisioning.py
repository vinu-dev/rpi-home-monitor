"""
WiFi hotspot provisioning module.

Provides a Flask blueprint for the first-boot setup wizard. When the RPi 4B
boots without WiFi configured, it creates a hotspot so the user can connect
from their phone and configure WiFi credentials + admin password.

Flow: collect all settings over the hotspot, then apply everything at once
in /complete (WiFi connect + admin password + stamp file + delayed hotspot
shutdown). This avoids killing the hotspot mid-wizard.

All setup endpoints require that /data/.setup-done does NOT exist. Once setup
is complete, the stamp file is written and all endpoints return 403.
"""
import logging
import os
import subprocess
import threading

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

# In-memory storage for WiFi credentials collected during setup.
# Only lives until /complete applies them. Never written to disk unencrypted.
_pending_wifi = {"ssid": "", "password": ""}


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


@provisioning_bp.route("/wifi/save", methods=["POST"])
def wifi_save():
    """Save WiFi credentials for later use.

    Does NOT connect immediately — credentials are held in memory and
    applied when /complete is called. This keeps the hotspot alive so
    the user can continue the setup wizard.

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

    _pending_wifi["ssid"] = ssid
    _pending_wifi["password"] = password

    log.info("WiFi credentials saved for SSID=%s (will apply at /complete)", ssid)
    return jsonify({"message": f"WiFi credentials saved for {ssid}"}), 200


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
    """Apply all settings and finish setup.

    This is the final step — called after the user has reviewed the
    summary and clicked "Apply & Finish". It:
      1. Connects to the saved WiFi network
      2. Gets the new IP address
      3. Writes the setup-done stamp file
      4. Returns the IP + hostname immediately
      5. Stops the hotspot after a 15-second delay

    If WiFi connection fails, returns an error so the user can fix it
    (the hotspot stays alive).
    """
    blocked = _require_setup_mode()
    if blocked:
        return blocked

    ssid = _pending_wifi.get("ssid", "")
    password = _pending_wifi.get("password", "")

    if not ssid or not password:
        return jsonify({"error": "WiFi credentials not saved. Go back and enter WiFi details."}), 400

    # --- Step 1: Connect to WiFi ---
    log.info("Connecting to WiFi: SSID=%s", ssid)
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
        return jsonify({"error": "WiFi connection timed out. Check your password and try again."}), 504
    except (FileNotFoundError, OSError) as exc:
        log.error("WiFi connect failed for SSID=%s: %s", ssid, exc)
        return jsonify({"error": f"WiFi connection failed: {exc}"}), 500

    if result.returncode != 0:
        stderr = result.stderr.strip()
        log.error("WiFi connect failed for SSID=%s: %s", ssid, stderr)
        if "secrets were required" in stderr.lower() or "no suitable" in stderr.lower():
            return jsonify({"error": "Incorrect WiFi password. Go back and try again."}), 401
        return jsonify({"error": "WiFi connection failed. Go back and try again.", "detail": stderr}), 500

    log.info("WiFi connected to %s", ssid)

    # --- Step 2: Get the new WiFi IP address ---
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
                    if "/" in addr:
                        addr = addr.split("/")[0]
                    if addr:
                        ip_address = addr
                        break
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # --- Step 3: Write the setup-done stamp file ---
    stamp = _setup_done_path()
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w") as f:
            f.write("setup completed\n")
        log.info("Stamp file written: %s", stamp)
    except OSError as exc:
        log.error("Failed to write stamp file %s: %s", stamp, exc)
        return jsonify({"error": f"Failed to mark setup complete: {exc}"}), 500

    # Clear saved credentials from memory
    _pending_wifi["ssid"] = ""
    _pending_wifi["password"] = ""

    # --- Step 4: Schedule delayed hotspot stop ---
    # The WiFi connect above already killed the hotspot (wlan0 switched
    # from AP to client mode), but we still call stop to clean up the
    # NM connection profile and LED state.
    def _delayed_hotspot_stop():
        log.info("Delayed hotspot cleanup triggered (15s elapsed)")
        try:
            result = subprocess.run(
                [HOTSPOT_SCRIPT, "stop"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            log.debug(
                "Hotspot stop: rc=%d stdout=%s",
                result.returncode,
                result.stdout.strip(),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            log.warning("Hotspot stop failed (non-fatal): %s", exc)

    timer = threading.Timer(15.0, _delayed_hotspot_stop)
    timer.daemon = True
    timer.start()

    log.info(
        "Setup complete! WiFi IP: %s — hotspot cleanup in 15 seconds",
        ip_address or "unknown",
    )

    return jsonify({
        "message": "Setup complete",
        "ip": ip_address,
        "hostname": "homemonitor.local",
    }), 200


@provisioning_bp.route("/wizard", methods=["GET"])
def setup_wizard():
    """Serve the setup wizard HTML page."""
    log.debug("Serving setup wizard")
    return render_template("setup.html")
