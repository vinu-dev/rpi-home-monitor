"""
WiFi setup for camera first boot.

On first boot (when /data/.setup-done doesn't exist):
1. Starts a WiFi hotspot "HomeCam-Setup"
2. Runs a tiny HTTP server on port 80 with a setup page
3. User enters WiFi SSID + password + server IP + camera password
4. Camera saves settings, tears down hotspot, connects to home WiFi
5. If WiFi fails, hotspot comes back up for retry

Single-radio constraint: wlan0 can either be an AP or a client, not both.
So we can't scan while hotspot is active. User types SSID manually or we
do a quick scan BEFORE starting the hotspot.
"""

import http.server
import json
import logging
import os
import threading
import time
from pathlib import Path

from camera_streamer import led, wifi

log = logging.getLogger("camera-streamer.wifi-setup")

SETUP_STAMP = ".setup-done"
LISTEN_PORT = 80
CONNECT_DELAY = 3

# Template directory (adjacent to this module)
_TEMPLATE_DIR = Path(__file__).parent / "templates"
_template_cache = {}


def _load_template(name):
    """Load an HTML template from the templates/ directory."""
    if name in _template_cache:
        return _template_cache[name]
    path = _TEMPLATE_DIR / name
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        log.error("Template not found: %s", path)
        html = f"<h1>Template Error</h1><p>Missing: {name}</p>"
    _template_cache[name] = html
    return html


def is_setup_complete(data_dir):
    """Check if camera has been configured."""
    return os.path.isfile(os.path.join(data_dir, SETUP_STAMP))


def mark_setup_complete(data_dir):
    """Write setup-done stamp file."""
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, SETUP_STAMP), "w") as f:
        f.write("1\n")


class WifiSetupServer:
    """HTTP server for camera WiFi and server configuration (first boot).

    Args:
        config: ConfigManager instance.
        wifi_interface: WiFi interface name (from Platform).
        hostname_prefix: Hostname prefix (from Platform).
    """

    def __init__(
        self, config, wifi_interface="wlan0", hostname_prefix="rpi-divinu-cam"
    ):
        self._config = config
        self._wifi_interface = wifi_interface
        self._hostname_prefix = hostname_prefix
        self._server = None
        self._thread = None
        self._hotspot_active = False
        self._cached_networks = []
        self._connect_result = None

    def needs_setup(self):
        """Return True if setup hasn't been completed."""
        return not is_setup_complete(self._config.data_dir)

    def start(self):
        """Scan networks, start hotspot, then start HTTP server."""
        if not self.needs_setup():
            log.info("Setup already complete, skipping")
            return False

        # Scan BEFORE starting hotspot (wlan0 is in client mode now)
        self._cached_networks = wifi.scan_networks(self._wifi_interface)
        log.info("Pre-scan found %d networks", len(self._cached_networks))

        if not wifi.start_hotspot(self._wifi_interface):
            log.warning("Could not start hotspot — setup via ethernet")
        else:
            self._hotspot_active = True

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
        """Stop HTTP server and hotspot."""
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._hotspot_active:
            wifi.stop_hotspot()
            self._hotspot_active = False
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
        """Background: wait for phone to get response, then connect."""
        time.sleep(CONNECT_DELAY)

        log.info("Tearing down hotspot, connecting to WiFi: %s", ssid)
        led.connecting()

        wifi.stop_hotspot()
        self._hotspot_active = False
        time.sleep(2)

        ok, err = wifi.connect_network(ssid, password, self._wifi_interface)

        if ok:
            log.info("WiFi connected! Saving config and completing setup.")
            self._config.update(SERVER_IP=server_ip, SERVER_PORT=server_port)
            mark_setup_complete(self._config.data_dir)
            self._set_unique_hostname()
            self._connect_result = True
            led.connected()
        else:
            log.error("WiFi connection failed: %s — restarting hotspot", err)
            self._connect_result = err or "Connection failed"
            led.error()
            time.sleep(2)
            if wifi.start_hotspot(self._wifi_interface):
                self._hotspot_active = True
            led.setup_mode()

    def _set_unique_hostname(self):
        """Set unique hostname using CPU serial suffix."""
        try:
            serial = ""
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("Serial"):
                        serial = line.split(":")[-1].strip()
            suffix = serial[-4:] if serial else "0000"
            hostname = f"{self._hostname_prefix}-{suffix}"
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
        """Rescan by briefly dropping AP, scanning, then restarting AP."""
        log.info("Rescan requested — dropping AP briefly")
        wifi.stop_hotspot()
        self._hotspot_active = False
        time.sleep(2)
        self._cached_networks = wifi.scan_networks(self._wifi_interface)
        log.info("Rescan found %d networks", len(self._cached_networks))
        time.sleep(1)
        if wifi.start_hotspot(self._wifi_interface):
            self._hotspot_active = True
        return self._cached_networks


# ============================================================
# Setup page HTTP handler (first boot — no auth required)
# ============================================================
def _make_handler(config, setup_server):
    """Create an HTTP request handler for first-boot setup."""

    class SetupHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            log.info("HTTP: " + format % args)

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
                self._json_response(
                    {
                        "status": status,
                        "error": error,
                        "setup_complete": is_setup_complete(config.data_dir),
                        "camera_id": config.camera_id,
                        "hostname": hostname,
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
            html = _load_template("setup.html").replace(
                "{{CAMERA_ID}}", config.camera_id
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SetupHandler
