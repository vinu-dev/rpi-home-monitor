"""
WiFi setup for camera first boot.

On first boot (when /data/.setup-done doesn't exist):
1. Starts a WiFi hotspot "HomeCam-Setup"
2. Runs a tiny HTTP server on port 80 with a setup page
3. User connects, enters WiFi SSID + password + server IP on ONE page
4. Camera saves settings, tears down hotspot, connects to home WiFi
5. If WiFi fails, hotspot comes back up for retry

Single-radio constraint: wlan0 can either be an AP or a client, not both.
So we can't scan while hotspot is active. User types SSID manually or we
do a quick scan BEFORE starting the hotspot.
"""
import http.server
import json
import os
import subprocess
import threading
import time
import logging

log = logging.getLogger("camera-streamer.wifi-setup")

HOTSPOT_SSID = "HomeCam-Setup"
HOTSPOT_PASS = "homecamera"
CONN_NAME = "HomeCam-Setup"
IFACE = "wlan0"
SETUP_STAMP = ".setup-done"
LISTEN_PORT = 80
# Seconds to wait after sending response before tearing down AP
CONNECT_DELAY = 3


def is_setup_complete(data_dir):
    """Check if camera has been configured."""
    return os.path.isfile(os.path.join(data_dir, SETUP_STAMP))


def mark_setup_complete(data_dir):
    """Write setup-done stamp file."""
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, SETUP_STAMP), "w") as f:
        f.write("1\n")


class WifiSetupServer:
    """HTTP server for camera WiFi and server configuration."""

    def __init__(self, config):
        self._config = config
        self._server = None
        self._thread = None
        self._hotspot_active = False
        self._cached_networks = []
        self._connect_result = None  # None=not tried, True=ok, str=error

    def needs_setup(self):
        """Return True if setup hasn't been completed."""
        return not is_setup_complete(self._config.data_dir)

    def start(self):
        """Scan networks, start hotspot, then start HTTP server."""
        if not self.needs_setup():
            log.info("Setup already complete, skipping")
            return False

        # Scan BEFORE starting hotspot (wlan0 is in client mode now)
        self._cached_networks = self._scan_wifi()
        log.info("Pre-scan found %d networks", len(self._cached_networks))

        if not self._start_hotspot():
            log.warning("Could not start hotspot — setup via ethernet")

        # Start HTTP server
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
        self._stop_hotspot()
        log.info("Setup server stopped")

    def save_and_connect(self, ssid, password, server_ip, server_port="8554"):
        """Save settings and attempt WiFi connection in background.

        Returns immediately so the HTTP response reaches the phone
        before the hotspot is torn down.
        """
        self._connect_result = None
        t = threading.Thread(
            target=self._do_connect,
            args=(ssid, password, server_ip, server_port),
            daemon=True,
            name="wifi-connect",
        )
        t.start()

    def _do_connect(self, ssid, password, server_ip, server_port):
        """Background: wait for phone to get response, then connect."""
        # Give the phone time to receive the HTTP response
        time.sleep(CONNECT_DELAY)

        log.info("Tearing down hotspot, connecting to WiFi: %s", ssid)

        # Tear down hotspot
        self._stop_hotspot()

        # Short pause for interface to settle
        time.sleep(2)

        # Try to connect
        ok, err = self._connect_wifi(ssid, password)

        if ok:
            log.info("WiFi connected! Saving config and completing setup.")
            self._config.update(SERVER_IP=server_ip, SERVER_PORT=server_port)
            mark_setup_complete(self._config.data_dir)
            self._connect_result = True
        else:
            log.error("WiFi connection failed: %s — restarting hotspot", err)
            self._connect_result = err or "Connection failed"
            # Restart hotspot so user can retry
            time.sleep(2)
            self._start_hotspot()

    def get_status(self):
        """Return current connection attempt status."""
        return self._connect_result

    def get_cached_networks(self):
        """Return networks from pre-hotspot scan."""
        return list(self._cached_networks)

    def rescan(self):
        """Rescan by briefly dropping AP, scanning, then restarting AP.

        This is disruptive — phone will disconnect briefly.
        """
        log.info("Rescan requested — dropping AP briefly")
        self._stop_hotspot()
        time.sleep(2)
        self._cached_networks = self._scan_wifi()
        log.info("Rescan found %d networks", len(self._cached_networks))
        time.sleep(1)
        self._start_hotspot()
        return self._cached_networks

    def _scan_wifi(self):
        """Scan for WiFi networks (only works when NOT in AP mode)."""
        try:
            # Force a fresh scan
            subprocess.run(
                ["nmcli", "device", "wifi", "rescan", "ifname", IFACE],
                capture_output=True, timeout=10,
            )
            time.sleep(3)  # Give scan time to complete

            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
                 "device", "wifi", "list", "ifname", IFACE],
                capture_output=True, text=True, timeout=15,
            )
            networks = []
            seen = set()
            for line in result.stdout.strip().splitlines():
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] and parts[0] not in seen:
                    seen.add(parts[0])
                    networks.append({
                        "ssid": parts[0],
                        "signal": int(parts[1]) if parts[1].isdigit() else 0,
                        "security": parts[2],
                    })
            networks.sort(key=lambda n: n["signal"], reverse=True)
            return networks
        except Exception as e:
            log.error("WiFi scan failed: %s", e)
            return []

    def _connect_wifi(self, ssid, password):
        """Connect to a WiFi network (must NOT be in AP mode)."""
        try:
            result = subprocess.run(
                [
                    "nmcli", "device", "wifi", "connect", ssid,
                    "password", password,
                    "ifname", IFACE,
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return True, ""
            err = result.stderr.strip() or result.stdout.strip()
            return False, err
        except subprocess.TimeoutExpired:
            return False, "Connection timed out"
        except Exception as e:
            return False, str(e)

    def _start_hotspot(self):
        """Start WiFi AP via NetworkManager."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "DEVICE", "device", "status"],
                capture_output=True, text=True, timeout=10,
            )
            if IFACE not in result.stdout:
                log.warning("WiFi interface %s not found", IFACE)
                return False

            # Remove old connection
            subprocess.run(
                ["nmcli", "connection", "delete", CONN_NAME],
                capture_output=True, timeout=10,
            )

            # Create AP with shared mode (auto dnsmasq DHCP)
            subprocess.run(
                [
                    "nmcli", "connection", "add",
                    "type", "wifi",
                    "ifname", IFACE,
                    "con-name", CONN_NAME,
                    "autoconnect", "no",
                    "ssid", HOTSPOT_SSID,
                    "wifi.mode", "ap",
                    "wifi.band", "bg",
                    "wifi-sec.key-mgmt", "wpa-psk",
                    "wifi-sec.psk", HOTSPOT_PASS,
                    "ipv4.method", "shared",
                ],
                capture_output=True, text=True, timeout=15, check=True,
            )

            subprocess.run(
                ["nmcli", "connection", "up", CONN_NAME],
                capture_output=True, text=True, timeout=15, check=True,
            )

            self._hotspot_active = True
            log.info("Hotspot started: SSID=%s", HOTSPOT_SSID)
            return True

        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired) as e:
            log.error("Failed to start hotspot: %s", e)
            return False

    def _stop_hotspot(self):
        """Stop and remove the hotspot connection."""
        if not self._hotspot_active:
            return
        try:
            subprocess.run(
                ["nmcli", "connection", "down", CONN_NAME],
                capture_output=True, timeout=10,
            )
            subprocess.run(
                ["nmcli", "connection", "delete", CONN_NAME],
                capture_output=True, timeout=10,
            )
            self._hotspot_active = False
            log.info("Hotspot stopped")
        except (subprocess.CalledProcessError, FileNotFoundError,
                subprocess.TimeoutExpired):
            pass


def _make_handler(config, setup_server):
    """Create an HTTP request handler with access to config/setup."""

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
                self._json_response({
                    "status": status,
                    "error": error,
                    "setup_complete": is_setup_complete(config.data_dir),
                    "camera_id": config.camera_id,
                })
            else:
                self.send_error(404)

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

                    if not ssid:
                        self._json_response({"error": "WiFi name required"}, 400)
                        return
                    if not password:
                        self._json_response({"error": "WiFi password required"}, 400)
                        return
                    if not server_ip:
                        self._json_response({"error": "Server IP required"}, 400)
                        return

                    # Start connection in background
                    setup_server.save_and_connect(
                        ssid, password, server_ip, server_port
                    )

                    self._json_response({
                        "status": "connecting",
                        "message": "Settings saved. Connecting to WiFi...",
                    })
                except json.JSONDecodeError:
                    self._json_response({"error": "Invalid request"}, 400)

            elif self.path == "/api/rescan":
                # Disruptive — will briefly drop AP
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
            html = _SETUP_HTML.replace(
                "{{CAMERA_ID}}", config.camera_id
            )
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return SetupHandler


_SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Camera Setup</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
       background: #1a1a2e; color: #eee; min-height: 100vh;
       display: flex; flex-direction: column; align-items: center; }
.container { max-width: 400px; width: 100%; padding: 20px; }
h1 { text-align: center; color: #e94560; margin: 30px 0 6px; font-size: 1.5em; }
.cam-id { text-align: center; color: #666; font-size: 0.8em; margin-bottom: 24px; }
.card { background: #16213e; border-radius: 12px; padding: 20px; margin: 12px 0; }
.card h3 { margin-bottom: 12px; font-size: 1.1em; }
label { display: block; margin: 12px 0 4px; color: #8090b0; font-size: 0.85em; }
input[type=text], input[type=password] {
  width: 100%; padding: 12px; border: 1px solid #2a3a5e; border-radius: 8px;
  background: #0f3460; color: #eee; font-size: 1em; outline: none; }
input:focus { border-color: #e94560; }
.btn { display: block; width: 100%; padding: 14px; border: none; border-radius: 10px;
  font-size: 1em; font-weight: 600; cursor: pointer; margin-top: 14px; }
.btn-primary { background: #e94560; color: #fff; }
.btn-primary:hover { background: #d63851; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { background: #0f3460; color: #b0b0c0; margin-top: 8px; }
.btn-secondary:hover { background: #133a6a; }
.hint { color: #506080; font-size: 0.8em; margin-top: 4px; }
.network-list { max-height: 200px; overflow-y: auto; margin: 8px 0; }
.net { padding: 10px 12px; border-bottom: 1px solid #0f3460; cursor: pointer;
  display: flex; justify-content: space-between; align-items: center; }
.net:hover { background: #0f3460; border-radius: 6px; }
.net-ssid { font-weight: 500; }
.net-signal { color: #4ade80; font-size: 0.85em; }
.msg { text-align: center; padding: 16px; border-radius: 8px; margin: 12px 0; }
.msg-info { background: #0f3460; color: #8090b0; }
.msg-ok { background: #065f46; color: #d1fae5; }
.msg-err { background: #7f1d1d; color: #fecaca; }
.msg-warn { background: #78350f; color: #fde68a; }
.spinner { display: inline-block; width: 18px; height: 18px;
  border: 2px solid rgba(255,255,255,0.2); border-top-color: #fff;
  border-radius: 50%; animation: spin 0.7s linear infinite;
  vertical-align: middle; margin-right: 8px; }
@keyframes spin { to { transform: rotate(360deg); } }
#view-form, #view-connecting, #view-result { display: none; }
#view-form.active, #view-connecting.active, #view-result.active { display: block; }
</style>
</head>
<body>
<div class="container">
  <h1>Camera Setup</h1>
  <p class="cam-id">{{CAMERA_ID}}</p>

  <!-- FORM VIEW -->
  <div id="view-form" class="active">
    <div class="card">
      <h3>WiFi Network</h3>
      <div id="net-section">
        <div id="net-list-wrap" style="display:none">
          <p style="color:#8090b0;font-size:0.85em;margin-bottom:6px">
            Networks found before hotspot started. Tap to select:</p>
          <div class="network-list" id="net-list"></div>
        </div>
        <label>WiFi Name (SSID)</label>
        <input type="text" id="in-ssid" placeholder="Your WiFi network name">
        <label>WiFi Password</label>
        <input type="password" id="in-pass" placeholder="WiFi password">
      </div>
    </div>

    <div class="card">
      <h3>Server Connection</h3>
      <label>Server IP Address</label>
      <input type="text" id="in-server" placeholder="192.168.1.100">
      <div class="hint">The IP of your RPi 4B running Home Monitor</div>
      <label>RTSP Port</label>
      <input type="text" id="in-port" value="8554">
    </div>

    <button class="btn btn-primary" id="btn-save" onclick="doConnect()">
      Save &amp; Connect
    </button>
    <button class="btn btn-secondary" onclick="loadNetworks()">
      Scan for Networks
    </button>
    <p class="hint" style="text-align:center;margin-top:8px">
      Scanning will briefly drop the hotspot connection.</p>
  </div>

  <!-- CONNECTING VIEW -->
  <div id="view-connecting">
    <div class="card">
      <div class="msg msg-info">
        <div class="spinner"></div> Connecting to WiFi...
      </div>
      <p style="color:#8090b0;font-size:0.85em;text-align:center;margin-top:12px">
        The hotspot will disappear now. This is normal.<br><br>
        <strong>If it worked:</strong> the camera LED will go solid.
        You can close this page.<br><br>
        <strong>If it failed:</strong> the <em>HomeCam-Setup</em> hotspot
        will reappear in about 30 seconds. Reconnect and try again.
      </p>
    </div>
  </div>

  <!-- RESULT VIEW (shown if user reconnects after failure) -->
  <div id="view-result">
    <div id="result-msg" class="msg msg-err"></div>
    <button class="btn btn-primary" onclick="showForm()">Try Again</button>
  </div>
</div>

<script>
function $(id) { return document.getElementById(id); }

function showView(name) {
  ['view-form','view-connecting','view-result'].forEach(function(v) {
    $(v).className = v === name ? 'active' : '';
  });
}

function showForm() { showView('view-form'); }

function loadNetworks() {
  var wrap = $('net-list-wrap');
  var list = $('net-list');
  list.innerHTML = '<div class="msg msg-info"><div class="spinner"></div> Scanning...</div>';
  wrap.style.display = 'block';

  fetch('/api/networks')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var nets = d.networks || [];
      if (nets.length === 0) {
        list.innerHTML = '<div class="msg msg-info">No networks found. Type SSID manually.</div>';
        return;
      }
      var html = '';
      nets.forEach(function(n) {
        html += '<div class="net" onclick="pickNet(\\''+esc(n.ssid)+'\\')"><span class="net-ssid">'
          +esc(n.ssid)+'</span><span class="net-signal">'+n.signal+'%</span></div>';
      });
      list.innerHTML = html;
    })
    .catch(function() {
      list.innerHTML = '<div class="msg msg-err">Scan failed. Type SSID manually.</div>';
    });
}

function pickNet(ssid) {
  $('in-ssid').value = ssid;
  $('in-pass').focus();
}

function doConnect() {
  var ssid = $('in-ssid').value.trim();
  var pass = $('in-pass').value;
  var server = $('in-server').value.trim();
  var port = $('in-port').value.trim() || '8554';

  if (!ssid) { alert('Enter WiFi network name'); return; }
  if (!pass) { alert('Enter WiFi password'); return; }
  if (!server) { alert('Enter server IP address'); return; }

  $('btn-save').disabled = true;

  fetch('/api/connect', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ssid:ssid, password:pass, server_ip:server, server_port:port})
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.error) {
      alert(d.error);
      $('btn-save').disabled = false;
    } else {
      showView('view-connecting');
    }
  })
  .catch(function() {
    showView('view-connecting');
  });
}

function esc(s) {
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML.replace(/'/g, "\\\\'");
}

// On page load: check if we're returning after a failed attempt
fetch('/api/status')
  .then(function(r) { return r.json(); })
  .then(function(d) {
    if (d.setup_complete) {
      showView('view-result');
      $('result-msg').className = 'msg msg-ok';
      $('result-msg').textContent = 'Setup complete! Camera is connected.';
    } else if (d.status === 'failed') {
      showView('view-result');
      $('result-msg').textContent = 'WiFi connection failed: ' + d.error + '. Try again.';
    } else {
      // Load cached networks
      fetch('/api/networks')
        .then(function(r) { return r.json(); })
        .then(function(nd) {
          var nets = nd.networks || [];
          if (nets.length > 0) {
            var list = $('net-list');
            var html = '';
            nets.forEach(function(n) {
              html += '<div class="net" onclick="pickNet(\\''+esc(n.ssid)+'\\')"><span class="net-ssid">'
                +esc(n.ssid)+'</span><span class="net-signal">'+n.signal+'%</span></div>';
            });
            list.innerHTML = html;
            $('net-list-wrap').style.display = 'block';
          }
        });
    }
  })
  .catch(function() {});
</script>
</body>
</html>
"""
