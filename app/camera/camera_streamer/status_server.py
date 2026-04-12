"""
Camera status page server (post-setup).

Runs on port 80 after first-boot setup is complete. Provides a
login-protected status page where the user can view camera info,
system health, and change WiFi settings.

Requires the admin password set during provisioning.
"""

import http.server
import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time

from camera_streamer import wifi

log = logging.getLogger("camera-streamer.status-server")

LISTEN_PORT = 80
SESSION_TIMEOUT = 7200  # 2 hours

# ---- Session store (in-memory) ----
_sessions = {}
_session_lock = threading.Lock()


def _create_session():
    """Create a new session token."""
    token = secrets.token_hex(32)
    with _session_lock:
        _sessions[token] = time.time() + SESSION_TIMEOUT
    return token


def _check_session(token):
    """Return True if session token is valid and not expired."""
    if not token:
        return False
    with _session_lock:
        expiry = _sessions.get(token)
        if expiry is None:
            return False
        if time.time() > expiry:
            del _sessions[token]
            return False
        _sessions[token] = time.time() + SESSION_TIMEOUT
        return True


def _destroy_session(token):
    """Remove a session."""
    if token:
        with _session_lock:
            _sessions.pop(token, None)


def _get_session_cookie(headers):
    """Extract session token from Cookie header."""
    cookie_header = headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith("cam_session="):
            return part.split("=", 1)[1]
    return ""


def _get_cpu_temp(thermal_path=None):
    """Read CPU temperature in Celsius."""
    path = thermal_path or "/sys/class/thermal/thermal_zone0/temp"
    try:
        with open(path) as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return 0.0


def _get_uptime():
    """Get human-readable uptime."""
    try:
        with open("/proc/uptime") as f:
            seconds = int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        seconds = 0
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _get_memory_mb():
    """Get total and used memory in MB."""
    try:
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        info = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 0) // 1024
        available = info.get("MemAvailable", 0) // 1024
        return total, total - available
    except (OSError, ValueError):
        return 0, 0


def _html_escape(s):
    """Escape HTML special characters."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _load_template(name):
    """Load an HTML template from the templates/ directory."""
    from pathlib import Path

    template_dir = Path(__file__).parent / "templates"
    try:
        return (template_dir / name).read_text(encoding="utf-8")
    except OSError:
        log.error("Template not found: %s", name)
        return f"<h1>Template Error</h1><p>Missing: {name}</p>"


class CameraStatusServer:
    """HTTP server showing camera status after setup.

    Runs on port 80. Requires login with the password set during
    provisioning. Shows camera ID, WiFi, server connection, stream
    status, system health, and a form to change WiFi.

    Args:
        config: ConfigManager instance.
        stream_manager: StreamManager instance (optional).
        wifi_interface: WiFi interface name (from Platform).
        thermal_path: Thermal sensor path (from Platform).
    """

    def __init__(
        self,
        config,
        stream_manager=None,
        wifi_interface="wlan0",
        thermal_path=None,
        pairing_manager=None,
    ):
        self._config = config
        self._stream = stream_manager
        self._wifi_interface = wifi_interface
        self._thermal_path = thermal_path
        self._pairing = pairing_manager
        self._server = None
        self._thread = None

    def start(self):
        """Start the status HTTP server on port 80."""
        handler = _make_status_handler(
            self._config,
            self._stream,
            self,
            self._wifi_interface,
            self._thermal_path,
            self._pairing,
        )
        try:
            self._server = http.server.HTTPServer(("0.0.0.0", LISTEN_PORT), handler)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="status-http",
            )
            self._thread.start()
            log.info("Status server listening on port %d", LISTEN_PORT)
            return True
        except Exception as e:
            log.error("Failed to start status server: %s", e)
            return False

    def stop(self):
        """Stop the status HTTP server."""
        if self._server:
            self._server.shutdown()
            self._server = None
            log.info("Status server stopped")

    def connect_wifi(self, ssid, password):
        """Connect to a new WiFi network. Returns (ok, error)."""
        return wifi.connect_network(ssid, password, self._wifi_interface)


_PAIR_PAGE_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Pair Camera — {{CAMERA_ID}}</title>
<style>
body{font-family:sans-serif;max-width:480px;margin:40px auto;padding:0 16px}
h1{font-size:1.4em}
.error{color:#c0392b;background:#fde8e8;padding:8px 12px;border-radius:4px;display:{{ERROR_DISPLAY}}}
.success{color:#27ae60;background:#e8fde8;padding:8px 12px;border-radius:4px;display:{{SUCCESS_DISPLAY}}}
label{display:block;margin-top:12px;font-weight:bold}
input{width:100%;padding:8px;margin-top:4px;box-sizing:border-box;font-size:1em}
button{margin-top:16px;padding:10px 24px;font-size:1em;cursor:pointer}
.status{margin-bottom:16px;padding:8px;background:#f0f0f0;border-radius:4px}
</style>
</head>
<body>
<h1>Pair Camera</h1>
<div class="status">Camera ID: {{CAMERA_ID}} | Status: {{PAIRED_STATUS}}</div>
<div class="error">{{ERROR}}</div>
<div class="success">{{SUCCESS}}</div>
<div style="display:{{FORM_DISPLAY}}">
<form method="POST" action="/pair">
<label>Server URL<input type="text" name="server_url" placeholder="https://192.168.1.100" required></label>
<label>PIN<input type="text" name="pin" pattern="[0-9]{6}" maxlength="6" placeholder="6-digit PIN from server" required></label>
<button type="submit">Pair</button>
</form>
</div>
<p><a href="/">Back to status</a></p>
</body>
</html>
"""


def _make_status_handler(
    config, stream_manager, status_server, wifi_interface, thermal_path, pairing_manager
):
    """Create HTTP handler for the camera status page."""

    class StatusHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            log.debug("Status HTTP: " + format % args)

        def _is_authenticated(self):
            if not config.has_password:
                return True
            token = _get_session_cookie(self.headers)
            return _check_session(token)

        def _require_auth(self):
            if self._is_authenticated():
                return True
            if self.path.startswith("/api/"):
                self._json_response({"error": "Authentication required"}, 401)
            else:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
            return False

        def do_GET(self):
            if self.path == "/login":
                self._serve_login_page()
            elif self.path == "/logout":
                token = _get_session_cookie(self.headers)
                _destroy_session(token)
                self.send_response(302)
                self.send_header(
                    "Set-Cookie", "cam_session=; Path=/; Max-Age=0; HttpOnly"
                )
                self.send_header("Location", "/login")
                self.end_headers()
            elif self.path == "/pair":
                # Pairing page is public — PIN serves as authentication
                self._serve_pair_page()
            elif self.path == "/" or self.path == "/status":
                if not self._require_auth():
                    return
                self._serve_status_page()
            elif self.path == "/api/status":
                if not self._require_auth():
                    return
                self._json_response(self._get_status())
            elif self.path == "/api/networks":
                if not self._require_auth():
                    return
                nets = wifi.scan_networks(wifi_interface)
                self._json_response({"networks": nets})
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()

        def do_POST(self):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b""

            if self.path == "/login":
                self._handle_login(body)
            elif self.path == "/pair" or self.path == "/api/pair":
                # PIN serves as authentication for pairing — no login required
                self._handle_pair(body)
            elif self.path == "/api/wifi":
                if not self._require_auth():
                    return
                try:
                    data = json.loads(body)
                    ssid = data.get("ssid", "").strip()
                    password = data.get("password", "")
                    if not ssid:
                        self._json_response({"error": "SSID required"}, 400)
                        return
                    if not password:
                        self._json_response({"error": "Password required"}, 400)
                        return
                    ok, err = status_server.connect_wifi(ssid, password)
                    if ok:
                        self._json_response({"message": f"Connected to {ssid}"})
                    else:
                        self._json_response({"error": err or "Connection failed"}, 500)
                except json.JSONDecodeError:
                    self._json_response({"error": "Invalid JSON"}, 400)
            elif self.path == "/api/factory-reset":
                if not self._require_auth():
                    return
                self._handle_factory_reset()
            elif self.path == "/api/password":
                if not self._require_auth():
                    return
                try:
                    data = json.loads(body)
                    current = data.get("current_password", "")
                    new_pw = data.get("new_password", "")
                    if not current or not new_pw:
                        self._json_response(
                            {"error": "Both current and new password required"}, 400
                        )
                        return
                    if len(new_pw) < 4:
                        self._json_response(
                            {"error": "Password must be at least 4 characters"}, 400
                        )
                        return
                    if not config.check_password(current):
                        self._json_response(
                            {"error": "Current password is incorrect"}, 403
                        )
                        return
                    config.set_password(new_pw)
                    config.save()
                    self._json_response({"message": "Password changed"})
                except json.JSONDecodeError:
                    self._json_response({"error": "Invalid JSON"}, 400)
            else:
                self.send_error(404)

        def _handle_login(self, body):
            username = ""
            password = ""
            content_type = self.headers.get("Content-Type", "")

            if "application/json" in content_type:
                try:
                    data = json.loads(body)
                    username = data.get("username", "").strip()
                    password = data.get("password", "")
                except json.JSONDecodeError:
                    self._json_response({"error": "Invalid JSON"}, 400)
                    return
            else:
                from urllib.parse import parse_qs

                params = parse_qs(body.decode("utf-8", errors="replace"))
                username = params.get("username", [""])[0].strip()
                password = params.get("password", [""])[0]

            if not username or not password:
                self._serve_login_page(error="Username and password required")
                return

            if username == config.admin_username and config.check_password(password):
                token = _create_session()
                log.info(
                    "Successful login from %s (user=%s)",
                    self.client_address[0],
                    username,
                )
                if "application/json" in content_type:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header(
                        "Set-Cookie",
                        f"cam_session={token}; Path=/; HttpOnly; SameSite=Strict",
                    )
                    resp = json.dumps({"message": "Login successful"}).encode()
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                else:
                    self.send_response(302)
                    self.send_header(
                        "Set-Cookie",
                        f"cam_session={token}; Path=/; HttpOnly; SameSite=Strict",
                    )
                    self.send_header("Location", "/")
                    self.end_headers()
            else:
                log.warning(
                    "Failed login from %s (user=%s)", self.client_address[0], username
                )
                if "application/json" in content_type:
                    self._json_response({"error": "Invalid username or password"}, 401)
                else:
                    self._serve_login_page(error="Invalid username or password")

        def _get_status(self):
            current_ssid = wifi.get_current_ssid()
            ip_addr = wifi.get_ip_address(wifi_interface)
            hostname = wifi.get_hostname()

            server_connected = False
            server_addr = config.server_ip or "unknown"
            if config.server_ip:
                import socket

                try:
                    socket.gethostbyname(config.server_ip)
                    server_connected = True
                except socket.gaierror:
                    pass

            streaming = False
            if stream_manager:
                streaming = stream_manager.is_streaming

            cpu_temp = _get_cpu_temp(thermal_path)
            uptime = _get_uptime()
            mem_total, mem_used = _get_memory_mb()

            return {
                "camera_id": config.camera_id,
                "hostname": hostname,
                "ip_address": ip_addr,
                "wifi_ssid": current_ssid,
                "server_address": server_addr,
                "server_connected": server_connected,
                "streaming": streaming,
                "cpu_temp": cpu_temp,
                "uptime": uptime,
                "memory_total_mb": mem_total,
                "memory_used_mb": mem_used,
            }

        def _json_response(self, data, code=200):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_login_page(self, error=""):
            html = (
                _load_template("login.html")
                .replace("{{CAMERA_ID}}", config.camera_id)
                .replace("{{ERROR}}", _html_escape(error))
                .replace("{{ERROR_DISPLAY}}", "block" if error else "none")
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_status_page(self):
            html = _load_template("status.html").replace(
                "{{CAMERA_ID}}", config.camera_id
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_pair_page(self, error="", success=""):
            is_paired = pairing_manager.is_paired if pairing_manager else False
            html = (
                _PAIR_PAGE_HTML.replace("{{CAMERA_ID}}", _html_escape(config.camera_id))
                .replace(
                    "{{PAIRED_STATUS}}",
                    "Paired" if is_paired else "Not paired",
                )
                .replace("{{ERROR}}", _html_escape(error))
                .replace("{{ERROR_DISPLAY}}", "block" if error else "none")
                .replace("{{SUCCESS}}", _html_escape(success))
                .replace("{{SUCCESS_DISPLAY}}", "block" if success else "none")
                .replace("{{FORM_DISPLAY}}", "none" if is_paired else "block")
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_pair(self, body):
            if not pairing_manager:
                self._json_response({"error": "Pairing not available"}, 500)
                return

            content_type = self.headers.get("Content-Type", "")
            pin = ""
            server_url = ""

            if "application/json" in content_type:
                try:
                    data = json.loads(body)
                    pin = data.get("pin", "").strip()
                    server_url = data.get("server_url", "").strip()
                except json.JSONDecodeError:
                    self._json_response({"error": "Invalid JSON"}, 400)
                    return
            else:
                from urllib.parse import parse_qs

                params = parse_qs(body.decode("utf-8", errors="replace"))
                pin = params.get("pin", [""])[0].strip()
                server_url = params.get("server_url", [""])[0].strip()

            if not pin or not server_url:
                if "application/json" in content_type:
                    self._json_response({"error": "PIN and server_url required"}, 400)
                else:
                    self._serve_pair_page(error="PIN and server URL are required")
                return

            ok, err = pairing_manager.exchange(pin, server_url)
            if "application/json" in content_type:
                if ok:
                    self._json_response({"message": "Pairing successful"})
                else:
                    self._json_response({"error": err}, 400)
            else:
                if ok:
                    self._serve_pair_page(
                        success="Pairing successful! Camera is now paired."
                    )
                else:
                    self._serve_pair_page(error=err)

        def _handle_factory_reset(self):
            """Wipe camera config, certs, and restart in setup mode."""
            data_dir = config.data_dir if hasattr(config, "data_dir") else "/data"
            errors = []

            # Remove config file
            config_path = os.path.join(data_dir, "config", "camera.conf")
            try:
                if os.path.exists(config_path):
                    os.remove(config_path)
            except OSError as e:
                errors.append(str(e))

            # Remove certificates (pairing data)
            certs_dir = os.path.join(data_dir, "certs")
            try:
                if os.path.exists(certs_dir):
                    shutil.rmtree(certs_dir)
            except OSError as e:
                errors.append(str(e))

            # Remove logs
            logs_dir = os.path.join(data_dir, "logs")
            try:
                if os.path.exists(logs_dir):
                    shutil.rmtree(logs_dir)
            except OSError as e:
                errors.append(str(e))

            if errors:
                log.warning("Factory reset completed with errors: %s", errors)
            else:
                log.info("Factory reset completed successfully")

            self._json_response({"message": "Factory reset complete. Restarting..."})

            # Schedule service restart
            def _restart():
                try:
                    subprocess.run(
                        ["systemctl", "restart", "camera-streamer"],
                        capture_output=True,
                        timeout=30,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                    log.error("Restart failed: %s", e)

            timer = threading.Timer(2.0, _restart)
            timer.daemon = True
            timer.start()

    return StatusHandler
