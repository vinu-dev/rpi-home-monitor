"""
Camera pairing manager — handles certificate exchange with server.

Implements the camera side of PIN-based pairing (ADR-0009):
1. Camera is unpaired (no /data/certs/client.crt)
2. Admin enters 6-digit PIN on camera status page
3. Camera POSTs PIN to server's /api/v1/pair/exchange
4. Server returns client cert, key, CA cert, pairing_secret
5. Camera stores certs at /data/certs/, pairing_secret in config

Design patterns:
- Constructor Injection (config, certs_dir)
- Single Responsibility (pairing lifecycle only)
"""

import json
import logging
import os
import ssl
import urllib.error
import urllib.request

log = logging.getLogger("camera-streamer.pairing")


class PairingManager:
    """Manages camera-side pairing with the server.

    Args:
        config: ConfigManager instance.
        certs_dir: Path to certificate directory (default: /data/certs).
    """

    def __init__(self, config, certs_dir=None):
        self._config = config
        self._certs_dir = certs_dir or os.path.join(
            os.environ.get("CAMERA_DATA_DIR", "/data"), "certs"
        )

    @property
    def is_paired(self):
        """Check if camera has been paired (client cert exists)."""
        return os.path.isfile(os.path.join(self._certs_dir, "client.crt"))

    @property
    def client_cert_path(self):
        return os.path.join(self._certs_dir, "client.crt")

    @property
    def client_key_path(self):
        return os.path.join(self._certs_dir, "client.key")

    @property
    def ca_cert_path(self):
        return os.path.join(self._certs_dir, "ca.crt")

    def exchange(self, pin, server_url):
        """Exchange PIN for certificates and pairing secret.

        Args:
            pin: 6-digit PIN string from admin dashboard.
            server_url: Server base URL (e.g., https://192.168.1.100).

        Returns:
            (success, error_message) tuple.
        """
        camera_id = self._config.camera_id
        if not camera_id:
            return False, "Camera ID not configured"

        url = f"{server_url}/api/v1/pair/exchange"
        payload = json.dumps({"pin": pin, "camera_id": camera_id}).encode("utf-8")

        try:
            # Skip TLS verification for first pairing (no CA cert yet)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
                error = body.get("error", f"HTTP {e.code}")
            except Exception:
                error = f"HTTP {e.code}"
            log.error("Pairing exchange failed: %s", error)
            return False, error

        except (urllib.error.URLError, OSError) as e:
            log.error("Cannot reach server at %s: %s", url, e)
            return False, f"Cannot reach server: {e}"

        # Store certificates
        try:
            self._store_certs(data)
        except (KeyError, OSError) as e:
            log.error("Failed to store certificates: %s", e)
            return False, f"Failed to store certificates: {e}"

        # Store pairing secret
        try:
            pairing_secret = data.get("pairing_secret", "")
            if pairing_secret:
                secret_path = os.path.join(self._certs_dir, "pairing_secret")
                with open(secret_path, "w") as f:
                    f.write(pairing_secret)
                os.chmod(secret_path, 0o600)
        except OSError as e:
            log.warning("Failed to store pairing secret: %s", e)

        log.info("Pairing successful — certificates stored at %s", self._certs_dir)
        return True, ""

    def _store_certs(self, data):
        """Write certificate files to disk."""
        os.makedirs(self._certs_dir, exist_ok=True)

        files = {
            "client.crt": data["client_cert"],
            "client.key": data["client_key"],
            "ca.crt": data["ca_cert"],
        }
        for filename, content in files.items():
            path = os.path.join(self._certs_dir, filename)
            with open(path, "w") as f:
                f.write(content)
            # Private key should be readable only by owner
            if filename.endswith(".key"):
                os.chmod(path, 0o600)

    def get_pairing_secret(self):
        """Read the stored pairing secret. Returns empty string if not found."""
        path = os.path.join(self._certs_dir, "pairing_secret")
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return ""
