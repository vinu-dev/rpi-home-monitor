"""
WiFi setup wizard for camera first boot.

On first boot (when /data/.setup-done doesn't exist):
1. Systemd starts camera-hotspot.service (WiFi AP "HomeCam-Setup")
2. This module runs an HTTP server on port 80 with a setup page
3. User enters WiFi SSID + password + server IP + camera password
4. Camera calls hotspot script to connect WiFi (stops AP atomically)
5. If WiFi fails, hotspot script restarts the AP for retry

Architecture (ADR-0013):
- Hotspot lifecycle is always managed by systemd + shell script
- This module only provides the HTTP setup wizard UI
- WiFi operations delegate to the hotspot script's 'connect' command

Single-radio constraint: wlan0 can either be an AP or a client, not both.
Networks are scanned BEFORE systemd starts the hotspot, via a pre-scan
in the lifecycle's _do_setup() or from a brief AP drop via /api/rescan.
"""

import http.server
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from camera_streamer import led, wifi

log = logging.getLogger("camera-streamer.wifi-setup")

SETUP_STAMP = ".setup-done"
LISTEN_PORT = 80
CONNECT_DELAY = 3
HOTSPOT_SCRIPT = "/opt/camera/scripts/camera-hotspot.sh"

# Template directory (adjacent to this module)
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_template_cache = {}


def _load_template(name, *, missing_fallback=None):
    """Load a text asset from the templates/ directory."""
    if name in _template_cache:
        return _template_cache[name]
    path = _TEMPLATE_DIR / name
    try:
        asset = path.read_text(encoding="utf-8")
    except OSError:
        log.error("Template not found: %s", path)
        if missing_fallback is None:
            asset = f"<h1>Template Error</h1><p>Missing: {name}</p>"
        else:
            asset = missing_fallback
    _template_cache[name] = asset
    return asset


_QRCODE_LIB = json.dumps(_load_template("vendor/qrcode.min.js", missing_fallback=""))


def is_setup_complete(data_dir):
    """Check if camera has been configured."""
    return os.path.isfile(os.path.join(data_dir, SETUP_STAMP))


def mark_setup_complete(data_dir):
    """Write setup-done stamp file."""
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, SETUP_STAMP), "w") as f:
        f.write("1\n")


# REQ: SWR-036; RISK: RISK-012; SEC: SC-012; TEST: TC-034
class WifiSetupServer:
    """HTTP server for camera WiFi and server configuration (first boot).

    Only provides the setup wizard UI. Hotspot lifecycle is managed
    by camera-hotspot.service (systemd). See ADR-0013.

    Args:
        config: ConfigManager instance.
        wifi_interface: WiFi interface name (from Platform).
        hostname_prefix: Hostname prefix (from Platform).
        hotspot_script: Path to hotspot management script.
    """

    def __init__(
        self,
        config,
        wifi_interface="wlan0",
        hostname_prefix="rpi-divinu-cam",
        hotspot_script=HOTSPOT_SCRIPT,
    ):
        self._config = config
        self._wifi_interface = wifi_interface
        self._hostname_prefix = hostname_prefix
        self._hotspot_script = hotspot_script
        self._server = None
        self._thread = None
        self._cached_networks = []
        self._connect_result = None
        self._expected_hostname = self._compute_hostname()

    def needs_setup(self):
        """Return True if setup hasn't been completed."""
        return not is_setup_complete(self._config.data_dir)

    def start(self):
        """Pre-scan networks and start HTTP setup wizard.

        The hotspot is already running (started by systemd).
        This method only starts the HTTP server for the setup UI.
        """
        if not self.needs_setup():
            log.info("Setup already complete, skipping")
            return False

        # Scan BEFORE hotspot is active (called from lifecycle before
        # systemd starts hotspot, or networks may be empty)
        self._cached_networks = wifi.scan_networks(self._wifi_interface)
        log.info("Pre-scan found %d networks", len(self._cached_networks))

        led.setup_mode()

        handler = _make_handler(self._config, self)
        self._server = http.server.HTTPServer(("0.0.0.0", LISTEN_PORT), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="setup-http"
        )
        self._thread.start()
        log.info("Setup server listening on port %d", LISTEN_PORT)
        return True

    def stop(self):
        """Stop HTTP server. Hotspot is managed by systemd."""
        if self._server:
            if self._thread and self._thread.is_alive():
                self._server.shutdown()
                self._thread.join(timeout=5)
            self._server.server_close()
            self._server = None
            self._thread = None
        led.off()
        log.info("Setup server stopped")

    def save_and_connect(
        self,
        ssid,
        password,
        server_ip,
        server_port="8554",
        admin_username="admin",
        admin_password="",
    ):
        """Save settings and attempt WiFi connection in background."""
        self._connect_result = None

        if admin_password:
            if admin_username:
                self._config.update(ADMIN_USERNAME=admin_username)
            self._config.set_password(admin_password)
            self._config.save()
            log.info(
                "Camera admin credentials set during provisioning (user=%s)",
                admin_username,
            )

        t = threading.Thread(
            target=self._do_connect,
            args=(ssid, password, server_ip, server_port),
            daemon=True,
            name="wifi-connect",
        )
        t.start()

    def _do_connect(self, ssid, password, server_ip, server_port):
        """Background: wait for phone to get response, then connect via hotspot script."""
        time.sleep(CONNECT_DELAY)

        log.info("Connecting to WiFi via hotspot script: %s", ssid)
        led.connecting()

        ok, err = self._hotspot_connect(ssid, password)

        if ok:
            log.info("WiFi connected! Saving config and completing setup.")
            self._config.update(SERVER_IP=server_ip, SERVER_PORT=server_port)
            mark_setup_complete(self._config.data_dir)
            self._set_unique_hostname()
            self._connect_result = True
            led.connected()
        else:
            log.error("WiFi connection failed: %s", err)
            self._connect_result = err or "Connection failed"
            led.error()

    def _hotspot_connect(self, ssid, password):
        """Connect to WiFi via hotspot script's 'connect' command.

        The script atomically stops the AP and connects to the target
        network. If connection fails, the script restarts the AP.
        Returns (success, error_message).
        """
        try:
            result = subprocess.run(
                [self._hotspot_script, "connect", ssid, password],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if result.returncode == 0:
                return True, ""
            output = result.stderr.strip() or result.stdout.strip()
            return False, output
        except FileNotFoundError:
            log.warning(
                "Hotspot script not found at %s — falling back to direct nmcli",
                self._hotspot_script,
            )
            return wifi.connect_network(ssid, password, self._wifi_interface)
        except subprocess.TimeoutExpired:
            return False, "Connection timed out"
        except OSError as e:
            return False, str(e)

    def _compute_hostname(self):
        """Pre-compute the hostname from CPU serial (same logic as _set_unique_hostname)."""
        try:
            serial = ""
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("Serial"):
                        serial = line.split(":")[-1].strip()
            suffix = serial[-4:] if serial else "0000"
            return f"{self._hostname_prefix}-{suffix}"
        except Exception:
            return ""

    def _set_unique_hostname(self):
        """Set unique hostname using CPU serial suffix."""
        hostname = self._expected_hostname
        if hostname:
            try:
                wifi.set_hostname(hostname)
            except Exception as e:
                log.warning("Failed to set hostname: %s", e)

    def get_status(self):
        """Return current connection attempt status."""
        return self._connect_result

    def get_cached_networks(self):
        """Return networks from pre-hotspot scan."""
        return list(self._cached_networks)

    def rescan(self):
        """Rescan by briefly dropping AP via hotspot script, scanning, then restarting."""
        log.info("Rescan requested — stopping hotspot briefly")
        try:
            subprocess.run(
                [self._hotspot_script, "stop"],
                capture_output=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            wifi.stop_hotspot()

        time.sleep(2)
        self._cached_networks = wifi.scan_networks(self._wifi_interface)
        log.info("Rescan found %d networks", len(self._cached_networks))
        time.sleep(1)

        try:
            subprocess.run(
                [self._hotspot_script, "start"],
                capture_output=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            wifi.start_hotspot(self._wifi_interface)

        return self._cached_networks


# ============================================================
# Setup page HTTP handler (first boot — no auth required)
# ============================================================
def _make_handler(config, setup_server):
    """Create an HTTP request handler for first-boot setup."""

    class SetupHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            log.info("HTTP: " + format % args)

        def do_HEAD(self):
            if self.path == "/" or self.path == "/setup":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
            elif self.path == "/api/networks" or self.path == "/api/status":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
            else:
                self.send_response(302)
                self.send_header("Location", "http://10.42.0.1/setup")
                self.end_headers()

        def do_GET(self):
            if self.path == "/" or self.path == "/setup":
                self._serve_setup_page()
            elif self.path == "/api/networks":
                networks = setup_server.get_cached_networks()
                self._json_response({"networks": networks})
            elif self.path == "/api/status":
                result = setup_server.get_status()
                if result is None:
                    status = "idle"
                    error = ""
                elif result is True:
                    status = "connected"
                    error = ""
                else:
                    status = "failed"
                    error = str(result)
                hostname = wifi.get_hostname()
                try:
                    ip_address = wifi.get_ip_address(setup_server._wifi_interface)
                except Exception:
                    log.warning(
                        "Unable to resolve setup-server IP address for %s",
                        setup_server._wifi_interface,
                        exc_info=True,
                    )
                    ip_address = ""
                self._json_response(
                    {
                        "status": status,
                        "error": error,
                        "setup_complete": is_setup_complete(config.data_dir),
                        "camera_id": config.camera_id,
                        "hostname": hostname,
                        "ip_address": ip_address,
                    }
                )
            else:
                log.debug("Captive portal redirect: %s -> /setup", self.path)
                self.send_response(302)
                self.send_header("Location", "http://10.42.0.1/setup")
                self.end_headers()

        def do_POST(self):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b""

            if self.path == "/api/connect":
                try:
                    data = json.loads(body)
                    ssid = data.get("ssid", "").strip()
                    password = data.get("password", "")
                    server_ip = data.get("server_ip", "").strip()
                    server_port = data.get("server_port", "8554").strip()
                    admin_username = data.get("admin_username", "admin").strip()
                    admin_password = data.get("admin_password", "")

                    if not ssid:
                        self._json_response({"error": "WiFi name required"}, 400)
                        return
                    if not password:
                        self._json_response({"error": "WiFi password required"}, 400)
                        return
                    if not server_ip:
                        self._json_response({"error": "Server IP required"}, 400)
                        return
                    if not admin_username or len(admin_username) < 3:
                        self._json_response(
                            {"error": "Username required (min 3 characters)"}, 400
                        )
                        return
                    if not admin_password or len(admin_password) < 4:
                        self._json_response(
                            {"error": "Password required (min 4 characters)"}, 400
                        )
                        return

                    setup_server.save_and_connect(
                        ssid,
                        password,
                        server_ip,
                        server_port,
                        admin_username=admin_username,
                        admin_password=admin_password,
                    )
                    self._json_response(
                        {
                            "status": "connecting",
                            "message": "Settings saved. Connecting to WiFi...",
                            "hostname": setup_server._expected_hostname,
                        }
                    )
                except json.JSONDecodeError:
                    self._json_response({"error": "Invalid request"}, 400)

            elif self.path == "/api/rescan":
                networks = setup_server.rescan()
                self._json_response({"networks": networks})
            else:
                self.send_error(404)

        def _json_response(self, data, code=200):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_setup_page(self):
            html = (
                _load_template("setup.html")
                .replace("{{CAMERA_ID}}", config.camera_id)
                .replace("{{HOSTNAME}}", setup_server._expected_hostname or "")
                .replace("{{QRCODE_LIB}}", _QRCODE_LIB)
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SetupHandler
