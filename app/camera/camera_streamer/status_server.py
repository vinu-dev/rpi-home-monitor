"""
Camera status page server (post-setup).

Runs on port 443 after first-boot setup is complete. Provides a
login-protected status page where the user can view camera info,
system health, and change WiFi settings.

Requires the admin password set during provisioning.
"""

import http.server
import json
import logging
import os
import secrets
import socket
import ssl
import subprocess
import threading
import time

from camera_streamer import ota_installer, wifi
from camera_streamer.control import ControlHandler, parse_control_request
from camera_streamer.factory_reset import FactoryResetService
from camera_streamer.server_notifier import notify_config_change

# Cap direct-upload bundle size: matches OTAAgent's cap. A 512 MB camera
# cannot stage anything larger without swapping out important pages.
MAX_BUNDLE_SIZE = 500 * 1024 * 1024

log = logging.getLogger("camera-streamer.status-server")

LISTEN_PORT = 443
SESSION_TIMEOUT = 7200  # 2 hours
TLS_CERT_NAME = "status.crt"
TLS_KEY_NAME = "status.key"

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


def _build_session_cookie(token):
    """Build a secure session cookie value."""
    return (
        f"cam_session={token}; Path=/; Max-Age={SESSION_TIMEOUT}; "
        "HttpOnly; Secure; SameSite=Strict"
    )


def _clear_session_cookie():
    """Build a secure expired session cookie value."""
    return "cam_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"


def _status_tls_paths(config):
    """Return cert/key paths for the camera status HTTPS endpoint."""
    certs_dir = config.certs_dir
    return (
        os.path.join(certs_dir, TLS_CERT_NAME),
        os.path.join(certs_dir, TLS_KEY_NAME),
    )


def _status_server_names():
    """Return hostname variants that should be present in the cert SAN."""
    names = []
    hostname = wifi.get_hostname() or socket.gethostname()
    if hostname:
        names.append(hostname)
        if "." not in hostname:
            names.append(f"{hostname}.local")
    names.extend(["localhost"])
    return list(dict.fromkeys(n for n in names if n))


def _ensure_tls_material(config):
    """Create a self-signed cert for the camera HTTPS status page if needed."""
    cert_path, key_path = _status_tls_paths(config)
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return cert_path, key_path

    os.makedirs(config.certs_dir, exist_ok=True)
    names = _status_server_names()
    common_name = names[0]
    san = ",".join(f"DNS:{name}" for name in names)
    san = f"{san},IP:127.0.0.1"

    try:
        subprocess.run(
            [
                "openssl",
                "ecparam",
                "-genkey",
                "-name",
                "prime256v1",
                "-out",
                key_path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-x509",
                "-key",
                key_path,
                "-out",
                cert_path,
                "-days",
                "1825",
                "-subj",
                f"/CN={common_name}",
                "-addext",
                f"subjectAltName={san}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        os.chmod(key_path, 0o600)
    except FileNotFoundError as e:
        raise RuntimeError("openssl is required for camera HTTPS status page") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(f"failed to generate camera HTTPS cert: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("timed out generating camera HTTPS cert") from e

    return cert_path, key_path


def _wrap_https_server(server, config):
    """Wrap the status server socket with TLS.

    Uses CERT_NONE so browsers (Chrome/Edge) can connect without being
    asked for a client certificate. Control API endpoints authenticate the
    server by its IP address (config.server_ip) which is set during pairing
    and is sufficient for a home LAN. Raw mTLS peer-cert verification is
    kept as a secondary check for clients that voluntarily present a cert.
    """
    cert_path, key_path = _ensure_tls_material(config)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # don't request client cert — breaks Chrome

    # Still load the CA so getpeercert() works if a client voluntarily sends one
    ca_path = os.path.join(config.certs_dir, "ca.crt")
    if os.path.isfile(ca_path):
        ctx.load_verify_locations(ca_path)
        log.info("CA loaded for peer-cert inspection (CA: %s)", ca_path)

    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server


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
    """HTTPS server showing camera status after setup.

    Runs on port 443. Requires login with the password set during
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
        stream_state_path=None,
    ):
        self._config = config
        self._stream = stream_manager
        self._wifi_interface = wifi_interface
        self._thermal_path = thermal_path
        self._pairing = pairing_manager
        # Let ControlHandler pick the default path when caller didn't override
        # so tests and production share the same default (ADR-0017).
        if stream_state_path is None:
            self._control = ControlHandler(config, stream_manager)
        else:
            self._control = ControlHandler(
                config, stream_manager, stream_state_path=stream_state_path
            )
        self._server = None
        self._thread = None

    @property
    def control_handler(self):
        """Return the internal ControlHandler (for lifecycle wiring)."""
        return self._control

    def start(self):
        """Start the status HTTPS server on port 443."""
        handler = _make_status_handler(
            self._config,
            self._stream,
            self,
            self._wifi_interface,
            self._thermal_path,
            self._pairing,
            self._control,
        )
        try:
            self._server = http.server.HTTPServer(("0.0.0.0", LISTEN_PORT), handler)
            self._server = _wrap_https_server(self._server, self._config)
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                daemon=True,
                name="status-https",
            )
            self._thread.start()
            log.info("Status server listening on HTTPS port %d", LISTEN_PORT)
            return True
        except Exception as e:
            log.error("Failed to start status server: %s", e)
            return False

    def stop(self):
        """Stop the status HTTPS server."""
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
button.danger{background:#c0392b;color:#fff;border:0;border-radius:4px}
button.danger:hover{background:#a53125}
.status{margin-bottom:16px;padding:8px;background:#f0f0f0;border-radius:4px}
.unpair-note{color:#666;font-size:.9em;margin-top:8px}
</style>
</head>
<body>
<h1>Pair Camera</h1>
<div class="status">Camera ID: {{CAMERA_ID}} | Status: {{PAIRED_STATUS}}</div>
<div class="error">{{ERROR}}</div>
<div class="success">{{SUCCESS}}</div>
<div style="display:{{FORM_DISPLAY}}">
<form method="POST" action="/pair">
<div style="display:{{SERVER_INFO_DISPLAY}}">
<div class="status">Server: {{SERVER_URL}}</div>
<input type="hidden" name="server_url" value="{{SERVER_URL}}">
</div>
<div style="display:{{SERVER_INPUT_DISPLAY}}">
<label>Server URL<input type="text" name="server_url" placeholder="https://your-server.local" required></label>
</div>
<label>PIN<input type="text" name="pin" pattern="[0-9]{6}" maxlength="6" placeholder="6-digit PIN from server" required autofocus></label>
<button type="submit">Pair</button>
</form>
</div>
<div style="display:{{UNPAIR_DISPLAY}}">
<p>This camera is paired with <strong>{{SERVER_URL}}</strong>. Forget this server to re-pair with a different one or clear the link entirely.</p>
<button id="unpair-btn" class="danger" type="button">Unpair / Forget server</button>
<p id="unpair-msg" class="unpair-note"></p>
</div>
<p><a href="/">Back to status</a></p>
<script>
(function(){
  var btn = document.getElementById('unpair-btn');
  if (!btn) return;
  btn.addEventListener('click', function(){
    if (!confirm('Forget this server?\\n\\nThe camera will stop streaming until it is paired again.')) {
      return;
    }
    btn.disabled = true;
    var msg = document.getElementById('unpair-msg');
    msg.textContent = 'Unpairing…';
    fetch('/api/unpair', {method:'POST', headers:{'Content-Type':'application/json'}})
      .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, body:j}; }); })
      .then(function(res){
        if (res.ok) {
          msg.textContent = 'Unpaired. Restarting camera service — page will reload shortly.';
          setTimeout(function(){ window.location.reload(); }, 6000);
        } else {
          btn.disabled = false;
          msg.textContent = 'Failed: ' + ((res.body && res.body.error) || 'unknown error');
        }
      })
      .catch(function(err){
        btn.disabled = false;
        msg.textContent = 'Failed: ' + err;
      });
  });
})();
</script>
</body>
</html>
"""


def _make_status_handler(
    config,
    stream_manager,
    status_server,
    wifi_interface,
    thermal_path,
    pairing_manager,
    control_handler=None,
):
    """Create HTTP handler for the camera status page."""

    class StatusHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            log.debug("Status HTTPS: " + format % args)

        def _has_mtls_client_cert(self):
            """Check if the request is from the paired server.

            Accepts the request if:
            - The client IP matches config.server_ip (set during pairing,
              either as an IP literal or as a hostname resolved to one), OR
            - The client voluntarily presented a valid TLS peer certificate.

            The SSL context uses CERT_NONE so browsers don't get a client-cert
            challenge (which breaks Chrome/Edge). Server IP check is the primary
            auth path; peer-cert is a fallback for future full mTLS enforcement.
            """
            server_ip = getattr(config, "server_ip", "") or ""
            if server_ip:
                client_ip = self.client_address[0]
                if client_ip == server_ip:
                    return True
                # server_ip may be a hostname (e.g. "rpi-divinu.local").
                # Resolve it so a fresh DNS/mDNS lookup matches the raw
                # TCP client IP we see here.
                try:
                    resolved = socket.gethostbyname(server_ip)
                except (socket.gaierror, socket.herror):
                    resolved = ""
                if resolved and client_ip == resolved:
                    return True
            # Fallback: TLS peer cert (only when client voluntarily sends one)
            try:
                peer_cert = self.request.getpeercert()
                return peer_cert is not None and len(peer_cert) > 0
            except (AttributeError, ValueError):
                return False

        def _require_mtls(self):
            """Require mTLS client certificate for control API endpoints."""
            if self._has_mtls_client_cert():
                return True
            self._json_response({"error": "Client certificate required"}, 401)
            return False

        def do_HEAD(self):
            if self.path == "/login":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
            elif self.path == "/logout":
                self.send_response(302)
                self.send_header("Set-Cookie", _clear_session_cookie())
                self.send_header("Location", "/login")
                self.end_headers()
            elif self.path == "/pair":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
            elif self.path == "/" or self.path == "/status":
                if not self._require_auth():
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
            elif self.path == "/api/status" or self.path == "/api/networks":
                if not self._require_auth():
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()

        def _is_authenticated(self):
            if not config.has_password:
                return True
            token = _get_session_cookie(self.headers)
            return _check_session(token)

        def _require_auth(self):
            """Require session auth OR mTLS for non-control API paths."""
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
            # Control API — mTLS auth
            if self.path == "/api/v1/control/config":
                if not self._require_mtls():
                    return
                self._json_response(control_handler.get_config())
                return
            if self.path == "/api/v1/control/capabilities":
                if not self._require_mtls():
                    return
                self._json_response(control_handler.get_capabilities())
                return
            if self.path == "/api/v1/control/status":
                if not self._require_mtls():
                    return
                self._json_response(control_handler.get_status())
                return
            if self.path == "/api/v1/control/stream/state":
                if not self._require_mtls():
                    return
                self._json_response(control_handler.get_stream_state())
                return

            if self.path == "/login":
                self._serve_login_page()
            elif self.path == "/logout":
                token = _get_session_cookie(self.headers)
                _destroy_session(token)
                self.send_response(302)
                self.send_header("Set-Cookie", _clear_session_cookie())
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
            elif self.path == "/api/ota/status":
                if not self._require_auth():
                    return
                self._json_response(ota_installer.read_status())
            else:
                self.send_response(302)
                self.send_header("Location", "/")
                self.end_headers()

        def do_PUT(self):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b""

            if self.path == "/api/v1/control/config":
                if not self._require_mtls():
                    return
                params, request_id, err = parse_control_request(body)
                if err:
                    self._json_response({"error": err}, 400)
                    return
                result, error, status = control_handler.set_config(params, request_id)
                if error:
                    self._json_response({"error": error}, status)
                else:
                    self._json_response(result, status)
            elif self.path == "/api/stream-config":
                if not self._require_auth():
                    return
                params, _, err = parse_control_request(body)
                if err:
                    self._json_response({"error": err}, 400)
                    return
                result, error, status = control_handler.set_config(
                    params, request_id=0, origin="local"
                )
                if error:
                    self._json_response({"error": error}, status)
                    return
                self._json_response(result, status)
                # Notify server of local config change (fire-and-forget)
                if (
                    result
                    and result.get("origin") == "local"
                    and result.get("status") == "ok"
                    and pairing_manager
                    and pairing_manager.is_paired
                    and config.server_ip
                ):
                    threading.Thread(
                        target=notify_config_change,
                        args=(config, pairing_manager),
                        daemon=True,
                        name="config-notify",
                    ).start()
            else:
                self.send_error(404)

        def do_POST(self):
            # OTA upload is streamed directly from self.rfile — must not
            # pre-buffer the body (bundles can be hundreds of MB).
            if self.path == "/api/ota/upload":
                if not self._require_auth():
                    return
                self._handle_ota_upload()
                return
            if self.path == "/api/ota/reboot":
                if not self._require_auth():
                    return
                self._handle_ota_reboot()
                return

            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b""

            # Control API — restart stream
            if self.path == "/api/v1/control/restart-stream":
                if not self._require_mtls():
                    return
                if stream_manager:
                    ok = stream_manager.restart()
                    self._json_response({"restarted": ok, "status": "ok"})
                else:
                    self._json_response({"error": "Stream manager not available"}, 503)
                return

            # Control API — on-demand stream start/stop (ADR-0017)
            if self.path == "/api/v1/control/stream/start":
                if not self._require_mtls():
                    return
                result, error, status = control_handler.set_stream_state("running")
                if error:
                    self._json_response({"error": error}, status)
                else:
                    self._json_response(result, status)
                return
            if self.path == "/api/v1/control/stream/stop":
                if not self._require_mtls():
                    return
                result, error, status = control_handler.set_stream_state("stopped")
                if error:
                    self._json_response({"error": error}, status)
                else:
                    self._json_response(result, status)
                return

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
            elif self.path == "/api/unpair":
                if not self._require_auth():
                    return
                self._handle_unpair()
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
                    self.send_header("Set-Cookie", _build_session_cookie(token))
                    resp = json.dumps({"message": "Login successful"}).encode()
                    self.send_header("Content-Length", str(len(resp)))
                    self.end_headers()
                    self.wfile.write(resp)
                else:
                    self.send_response(302)
                    self.send_header("Set-Cookie", _build_session_cookie(token))
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

            paired = pairing_manager.is_paired if pairing_manager else False

            return {
                "camera_id": config.camera_id,
                "hostname": hostname,
                "ip_address": ip_addr,
                "wifi_ssid": current_ssid,
                "server_address": server_addr,
                "server_connected": server_connected,
                "streaming": streaming,
                "paired": paired,
                "cpu_temp": cpu_temp,
                "uptime": uptime,
                "memory_total_mb": mem_total,
                "memory_used_mb": mem_used,
                "stream_config": {
                    "width": config.width,
                    "height": config.height,
                    "fps": config.fps,
                    "bitrate": config.bitrate,
                    "h264_profile": config.h264_profile,
                    "rotation": config.rotation,
                    "hflip": config.hflip,
                    "vflip": config.vflip,
                },
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
            server_url = config.server_https_url
            has_server = bool(server_url)
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
                .replace("{{UNPAIR_DISPLAY}}", "block" if is_paired else "none")
                .replace("{{SERVER_URL}}", _html_escape(server_url))
                .replace("{{SERVER_INFO_DISPLAY}}", "block" if has_server else "none")
                .replace("{{SERVER_INPUT_DISPLAY}}", "none" if has_server else "block")
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

            if not server_url:
                server_url = config.server_https_url

            if not pin or not server_url:
                if "application/json" in content_type:
                    self._json_response({"error": "PIN and server_url required"}, 400)
                else:
                    self._serve_pair_page(error="PIN and server URL are required")
                return

            # Re-pair flow: if this camera is already paired (stale certs from
            # a previous server that has since forgotten us), wipe local state
            # first. Otherwise a failed exchange would leave the GUI reading
            # "Paired" because is_paired just checks the cert file's presence.
            if pairing_manager.is_paired:
                pairing_manager.reset_local_state()

            ok, err = pairing_manager.exchange(pin, server_url)
            if "application/json" in content_type:
                if ok:
                    self._json_response({"message": "Pairing successful — restarting"})
                else:
                    self._json_response({"error": err}, 400)
            else:
                if ok:
                    self._serve_pair_page(
                        success="Pairing successful! Camera is restarting…"
                    )
                else:
                    self._serve_pair_page(error=err)

            if ok:
                # Restart the service so the lifecycle enters RUNNING state
                # cleanly with the new client cert and starts the heartbeat
                # sender. Without a restart, a camera that was auto-wiped
                # (heartbeat 401 path) would have a dead heartbeat thread and
                # would never send heartbeats even though certs are now valid.
                def _restart_after_pair():
                    import time as _t

                    _t.sleep(0.5)
                    import os as _os
                    import signal as _sig

                    try:
                        _os.kill(_os.getpid(), _sig.SIGTERM)
                    except OSError:
                        pass

                import threading as _t2

                _t2.Thread(target=_restart_after_pair, daemon=True).start()

        def _handle_ota_upload(self):
            """Stream a .swu bundle from the browser into the OTA spool,
            then fire the root installer via the trigger-file protocol.

            The bundle is streamed straight to disk — never buffered in
            RAM — so 512 MB cameras can accept 200+ MB updates without
            OOM pressure. After staging, we write the trigger and return
            200 immediately; the browser polls /api/ota/status to watch
            the install progress. If we blocked here waiting on
            wait_for_completion() the HTTP request would be held open
            for several minutes and proxies/browsers would time out.
            """
            try:
                content_len = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._json_response({"error": "Invalid Content-Length"}, 400)
                return
            if content_len <= 0:
                self._json_response({"error": "Empty upload"}, 400)
                return
            if content_len > MAX_BUNDLE_SIZE:
                self._json_response(
                    {"error": f"Bundle too large (limit {MAX_BUNDLE_SIZE} bytes)"},
                    413,
                )
                return
            if ota_installer.is_busy():
                self._json_response(
                    {"error": "Another update is already in progress"}, 409
                )
                return

            ok, msg = ota_installer.stage_bundle(self.rfile, content_len)
            if not ok:
                self._json_response({"error": msg}, 500)
                return

            ok, msg = ota_installer.trigger_install(msg)
            if not ok:
                self._json_response({"error": msg}, 500)
                return

            self._json_response(
                {"message": "Install triggered", "bundle_bytes": content_len}
            )

        def _handle_ota_reboot(self):
            """Reboot the camera after a successful install.

            We only honour reboot when status.json is in the 'installed'
            terminal state — rebooting mid-install would brick the
            standby slot. HTTP response is flushed before reboot runs
            so the browser sees the 200 before the network drops.
            """
            status = ota_installer.read_status()
            state = status.get("state")
            if state != ota_installer.STATE_INSTALLED:
                self._json_response(
                    {
                        "error": (
                            f"No installed update to apply (current state: {state})"
                        )
                    },
                    400,
                )
                return

            self._json_response({"message": "Rebooting"})

            def _reboot():
                time.sleep(1.0)
                try:
                    subprocess.run(["reboot"], check=False, timeout=15)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    log.error("reboot command failed: %s", exc)

            threading.Thread(target=_reboot, daemon=True, name="ota-reboot").start()

        def _handle_factory_reset(self):
            """Wipe camera config, certs, and restart in setup mode.

            Delegates to FactoryResetService for consistent behavior
            with the server's factory reset (ADR-0013).
            """
            data_dir = config.data_dir if hasattr(config, "data_dir") else "/data"
            reset_svc = FactoryResetService(config, data_dir)
            msg, status = reset_svc.execute_reset()
            self._json_response({"message": msg}, status)

        def _handle_unpair(self):
            """Camera-initiated unpair ("forget this server").

            Steps — kept deliberately simple, modelled on Bluetooth's
            "Forget device" UX:
              1. Best-effort signed goodbye to the server so the dashboard
                 updates immediately (pairing_service.unpair on that side).
              2. Wipe local client.crt / client.key / pairing_secret. From
                 this moment is_paired returns False.
              3. Send the JSON response to the browser before we pull the
                 rug — if we restart first the response never lands and the
                 user sees a spinner forever.
              4. SIGTERM ourselves on a background thread so the HTTP
                 response flushes first. systemd's Restart=always respawns
                 camera-streamer; the lifecycle re-enters PAIRING and the
                 /pair page shows the PIN form again in a few seconds.
                 (Calling "systemctl restart" on our own unit from inside
                 the unit is unreliable — see heartbeat._handle_server_unpair
                 for the full rationale.)

            If goodbye fails (server offline), we still wipe locally and
            restart — the server will reconcile via mDNS paired=false or
            the 30s OFFLINE_TIMEOUT. This matches Bluetooth: either side
            can forget independently.
            """
            if pairing_manager is None or not pairing_manager.is_paired:
                self._json_response({"error": "Camera is not currently paired"}, 400)
                return

            server_url = config.server_https_url or ""
            goodbye_ok, goodbye_err = False, ""
            try:
                goodbye_ok, goodbye_err = pairing_manager.send_goodbye(server_url)
            except Exception as exc:  # never let goodbye block the unpair
                log.warning("send_goodbye raised: %s", exc)

            # Local wipe is authoritative — we are unpaired from this moment.
            pairing_manager.reset_local_state()

            self._json_response(
                {
                    "message": "Camera unpaired",
                    "goodbye_acknowledged": goodbye_ok,
                    "goodbye_error": "" if goodbye_ok else goodbye_err,
                }
            )

            # Schedule SIGTERM on a background thread so the HTTP response
            # is flushed to the client before the process exits.
            def _restart():
                import time as _t

                _t.sleep(0.5)
                import os as _os
                import signal as _sig

                try:
                    _os.kill(_os.getpid(), _sig.SIGTERM)
                except OSError:
                    pass

            import threading as _threading

            _threading.Thread(target=_restart, daemon=True).start()

    return StatusHandler
