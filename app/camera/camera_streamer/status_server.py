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
import secrets
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
        self, config, stream_manager=None, wifi_interface="wlan0", thermal_path=None
    ):
        self._config = config
        self._stream = stream_manager
        self._wifi_interface = wifi_interface
        self._thermal_path = thermal_path
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


def _make_status_handler(
    config, stream_manager, status_server, wifi_interface, thermal_path
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

    return StatusHandler
