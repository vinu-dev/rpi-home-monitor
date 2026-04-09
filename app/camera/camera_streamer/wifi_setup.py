"""
WiFi setup for camera first boot.

On first boot (when /data/.setup-done doesn't exist):
1. Starts a WiFi hotspot "HomeCam-Setup"
2. Runs a tiny HTTP server on port 80 with a setup page
3. User connects, configures WiFi + server IP
4. Camera connects to home WiFi and starts streaming

Uses the same NetworkManager shared-mode pattern as the server.
"""
import http.server
import json
import os
import subprocess
import threading
import logging
import urllib.parse

log = logging.getLogger("camera-streamer.wifi-setup")

HOTSPOT_SSID = "HomeCam-Setup"
HOTSPOT_PASS = "homecamera"
CONN_NAME = "HomeCam-Setup"
IFACE = "wlan0"
SETUP_STAMP = ".setup-done"
LISTEN_PORT = 80


def is_setup_complete(data_dir):
    """Check if camera has been configured."""
    return os.path.isfile(os.path.join(data_dir, SETUP_STAMP))


def mark_setup_complete(data_dir):
    """Write setup-done stamp file."""
    with open(os.path.join(data_dir, SETUP_STAMP), "w") as f:
        f.write("1\n")


class WifiSetupServer:
    """HTTP server for camera WiFi and server configuration."""

    def __init__(self, config):
        self._config = config
        self._server = None
        self._thread = None
        self._hotspot_active = False

    def needs_setup(self):
        """Return True if setup hasn't been completed."""
        return not is_setup_complete(self._config.data_dir)

    def start(self):
        """Start hotspot and setup HTTP server."""
        if not self.needs_setup():
            log.info("Setup already complete, skipping")
            return False

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

    def complete_setup(self, server_ip, server_port="8554"):
        """Finalize setup: save config, stop hotspot, mark done."""
        self._config.update(SERVER_IP=server_ip, SERVER_PORT=server_port)
        mark_setup_complete(self._config.data_dir)
        log.info("Setup complete — server=%s:%s", server_ip, server_port)

    def _start_hotspot(self):
        """Start WiFi AP via NetworkManager."""
        try:
            # Check if wlan0 exists
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
            log.info("Hotspot started: SSID=%s, password=%s", HOTSPOT_SSID, HOTSPOT_PASS)
            return True

        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
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
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def connect_wifi(self, ssid, password):
        """Connect to a WiFi network."""
        try:
            # If hotspot is active, bring it down first
            if self._hotspot_active:
                subprocess.run(
                    ["nmcli", "connection", "down", CONN_NAME],
                    capture_output=True, timeout=10,
                )

            result = subprocess.run(
                [
                    "nmcli", "device", "wifi", "connect", ssid,
                    "password", password,
                    "ifname", IFACE,
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                log.info("Connected to WiFi: %s", ssid)
                return True, ""
            else:
                err = result.stderr.strip() or result.stdout.strip()
                log.error("WiFi connect failed: %s", err)
                # Re-activate hotspot so user can retry
                if self._hotspot_active:
                    subprocess.run(
                        ["nmcli", "connection", "up", CONN_NAME],
                        capture_output=True, timeout=15,
                    )
                return False, err
        except Exception as e:
            log.error("WiFi connect error: %s", e)
            return False, str(e)

    def scan_wifi(self):
        """Scan for available WiFi networks."""
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
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


def _make_handler(config, setup_server):
    """Create an HTTP request handler with access to config/setup."""

    class SetupHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            log.info("HTTP: " + format % args)

        def do_GET(self):
            if self.path == "/" or self.path == "/setup":
                self._serve_setup_page()
            elif self.path == "/api/wifi/scan":
                networks = setup_server.scan_wifi()
                self._json_response({"networks": networks})
            elif self.path == "/api/status":
                self._json_response({
                    "setup_complete": is_setup_complete(config.data_dir),
                    "camera_id": config.camera_id,
                })
            else:
                self.send_error(404)

        def do_POST(self):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len > 0 else b""

            if self.path == "/api/wifi/connect":
                try:
                    data = json.loads(body)
                    ssid = data.get("ssid", "")
                    password = data.get("password", "")
                    if not ssid:
                        self._json_response({"error": "SSID required"}, 400)
                        return
                    ok, err = setup_server.connect_wifi(ssid, password)
                    if ok:
                        self._json_response({"status": "connected", "ssid": ssid})
                    else:
                        self._json_response({"error": err or "Connection failed"}, 400)
                except json.JSONDecodeError:
                    self._json_response({"error": "Invalid JSON"}, 400)

            elif self.path == "/api/setup/complete":
                try:
                    data = json.loads(body)
                    server_ip = data.get("server_ip", "")
                    server_port = data.get("server_port", "8554")
                    if not server_ip:
                        self._json_response({"error": "server_ip required"}, 400)
                        return
                    setup_server.complete_setup(server_ip, server_port)
                    self._json_response({"status": "complete"})
                except json.JSONDecodeError:
                    self._json_response({"error": "Invalid JSON"}, 400)
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
            html = _SETUP_HTML.replace("{{CAMERA_ID}}", config.camera_id)
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
h1 { text-align: center; color: #e94560; margin: 30px 0 10px; font-size: 1.4em; }
h2 { text-align: center; color: #aaa; font-size: 0.9em; margin-bottom: 30px; }
.step { display: none; }
.step.active { display: block; }
.card { background: #16213e; border-radius: 12px; padding: 20px; margin: 15px 0; }
label { display: block; margin: 10px 0 5px; color: #aaa; font-size: 0.85em; }
input[type=text], input[type=password] {
  width: 100%; padding: 12px; border: 1px solid #333; border-radius: 8px;
  background: #0f3460; color: #eee; font-size: 1em; }
button { width: 100%; padding: 14px; border: none; border-radius: 8px;
  background: #e94560; color: #fff; font-size: 1em; font-weight: 600;
  cursor: pointer; margin-top: 15px; }
button:disabled { opacity: 0.5; }
.network-list { max-height: 250px; overflow-y: auto; }
.network { padding: 12px; border-bottom: 1px solid #333; cursor: pointer;
  display: flex; justify-content: space-between; }
.network:hover { background: #0f3460; }
.signal { color: #e94560; font-weight: bold; }
.status { text-align: center; padding: 15px; color: #4CAF50; }
.error { color: #e94560; text-align: center; padding: 10px; }
.spinner { display: inline-block; width: 20px; height: 20px;
  border: 3px solid #333; border-top: 3px solid #e94560;
  border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="container">
  <h1>Camera Setup</h1>
  <h2>{{CAMERA_ID}}</h2>

  <!-- Step 1: WiFi -->
  <div class="step active" id="step-wifi">
    <div class="card">
      <h3>Connect to WiFi</h3>
      <p style="color:#aaa;font-size:0.85em;margin:10px 0">
        Select your home WiFi network.</p>
      <div id="wifi-loading" style="text-align:center;padding:20px">
        <div class="spinner"></div><p style="margin-top:10px;color:#aaa">Scanning...</p>
      </div>
      <div class="network-list" id="network-list" style="display:none"></div>
      <div id="wifi-form" style="display:none">
        <label>Network: <strong id="selected-ssid"></strong></label>
        <label>Password</label>
        <input type="password" id="wifi-pass" placeholder="WiFi password">
        <button onclick="connectWifi()">Connect</button>
        <button onclick="showNetworks()" style="background:#333;margin-top:8px">Back</button>
      </div>
      <div id="wifi-status"></div>
    </div>
  </div>

  <!-- Step 2: Server -->
  <div class="step" id="step-server">
    <div class="card">
      <h3>Server Connection</h3>
      <p style="color:#aaa;font-size:0.85em;margin:10px 0">
        Enter your Home Monitor server IP address.</p>
      <label>Server IP</label>
      <input type="text" id="server-ip" placeholder="192.168.1.100">
      <label>Port (default: 8554)</label>
      <input type="text" id="server-port" value="8554">
      <button onclick="completeSetup()">Complete Setup</button>
    </div>
  </div>

  <!-- Step 3: Done -->
  <div class="step" id="step-done">
    <div class="card">
      <div class="status">
        <h3 style="font-size:2em;margin-bottom:10px">&#10003;</h3>
        <h3>Setup Complete!</h3>
        <p style="margin-top:10px;color:#aaa">
          Camera will now connect to the server and start streaming.
          You can close this page.</p>
      </div>
    </div>
  </div>
</div>

<script>
let selectedSSID = '';

async function scanWifi() {
  try {
    const r = await fetch('/api/wifi/scan');
    const d = await r.json();
    const list = document.getElementById('network-list');
    list.innerHTML = '';
    d.networks.forEach(n => {
      const div = document.createElement('div');
      div.className = 'network';
      div.innerHTML = '<span>'+n.ssid+'</span><span class="signal">'+n.signal+'%</span>';
      div.onclick = () => selectNetwork(n.ssid);
      list.appendChild(div);
    });
    document.getElementById('wifi-loading').style.display = 'none';
    list.style.display = 'block';
  } catch(e) {
    document.getElementById('wifi-loading').innerHTML =
      '<p class="error">Scan failed. Retrying...</p>';
    setTimeout(scanWifi, 3000);
  }
}

function selectNetwork(ssid) {
  selectedSSID = ssid;
  document.getElementById('selected-ssid').textContent = ssid;
  document.getElementById('network-list').style.display = 'none';
  document.getElementById('wifi-form').style.display = 'block';
}

function showNetworks() {
  document.getElementById('wifi-form').style.display = 'none';
  document.getElementById('network-list').style.display = 'block';
}

async function connectWifi() {
  const pass = document.getElementById('wifi-pass').value;
  const status = document.getElementById('wifi-status');
  status.innerHTML = '<div style="text-align:center;padding:10px"><div class="spinner"></div></div>';
  try {
    const r = await fetch('/api/wifi/connect', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ssid: selectedSSID, password: pass})
    });
    const d = await r.json();
    if (r.ok) {
      status.innerHTML = '<p class="status">Connected to ' + selectedSSID + '</p>';
      setTimeout(() => showStep('step-server'), 1000);
    } else {
      status.innerHTML = '<p class="error">' + (d.error || 'Failed') + '</p>';
    }
  } catch(e) {
    status.innerHTML = '<p class="error">Connection error</p>';
  }
}

async function completeSetup() {
  const ip = document.getElementById('server-ip').value.trim();
  const port = document.getElementById('server-port').value.trim() || '8554';
  if (!ip) { alert('Server IP is required'); return; }
  try {
    const r = await fetch('/api/setup/complete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({server_ip: ip, server_port: port})
    });
    if (r.ok) { showStep('step-done'); }
  } catch(e) { alert('Error: ' + e); }
}

function showStep(id) {
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
}

scanWifi();
</script>
</body>
</html>
"""
