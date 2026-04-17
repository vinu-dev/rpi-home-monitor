"""
Camera pairing manager — handles certificate exchange with server.

Implements the camera side of PIN-based pairing (ADR-0009):
1. Camera is unpaired (no /data/certs/client.crt)
2. Admin enters 6-digit PIN on camera status page
3. Camera POSTs PIN to server's /api/v1/pair/exchange
4. Server returns client cert, key, CA cert, pairing_secret
5. Camera stores certs at /data/certs/, pairing_secret in config

TLS security (TOFU — trust on first use):
- Before exchange the camera has no CA cert to verify the server's TLS cert.
- We fetch the server CA cert from /api/v1/setup/ca-cert (public, no auth)
  over plain HTTP and load it in-memory to verify the HTTPS exchange call.
- This prevents a passive MITM from intercepting the client cert undetected:
  if the attacker serves a fake CA cert, subsequent mTLS connections to the
  real server will fail and the admin will be alerted.
- If the CA cert fetch also fails (no network), we fall back to CERT_NONE
  with a warning (same behaviour as before, now the explicit last resort).
- Reference: RFC 8555 §10.2 (ACME TOFU pattern).

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

        Uses TOFU (trust on first use) TLS verification: fetches the server's
        CA cert before the exchange and uses it to verify the TLS connection.
        Falls back to unverified only if the CA cert cannot be fetched.

        Args:
            pin: 6-digit PIN string from admin dashboard.
            server_url: Server base URL (e.g., https://192.168.1.100).

        Returns:
            (success, error_message) tuple.
        """
        camera_id = self._config.camera_id
        if not camera_id:
            return False, "Camera ID not configured"

        # Build TLS context (TOFU if no CA cert on disk yet)
        tls_ctx = self._build_tls_context(server_url)

        url = f"{server_url}/api/v1/pair/exchange"
        payload = json.dumps({"pin": pin, "camera_id": camera_id}).encode("utf-8")

        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, context=tls_ctx, timeout=30) as resp:
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

    def _fetch_server_ca_cert(self, server_url):
        """Fetch the server CA certificate for TOFU verification.

        Tries plain HTTP so this works both over the hotspot (no TLS) and
        once the server is on the LAN. The CA cert is public; it is not a
        secret, so serving it over HTTP is intentional.

        Returns the PEM string on success, or empty string on failure.
        """
        base = server_url.rstrip("/")
        # Derive HTTP URL — try over HTTP so no TLS chicken-and-egg problem
        if base.startswith("https://"):
            http_base = "http://" + base[len("https://") :]
        else:
            http_base = base

        url = f"{http_base}/api/v1/setup/ca-cert"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                pem = resp.read().decode("utf-8")
                if "BEGIN CERTIFICATE" in pem:
                    return pem
                log.debug("Server /setup/ca-cert returned unexpected content")
        except Exception as e:
            log.debug("Could not fetch server CA cert for TOFU: %s", e)
        return ""

    def _build_tls_context(self, server_url):
        """Build a TLS context for the pairing exchange request.

        Priority:
        1. Existing CA cert on disk (re-pairing after unpair) — full verification.
        2. TOFU: fetch CA cert from server over HTTP, verify exchange with it.
        3. Last resort: no verification (logs a warning).
        """
        # Already have a CA cert from a previous pairing — use it
        if os.path.isfile(self.ca_cert_path):
            ctx = ssl.create_default_context(cafile=self.ca_cert_path)
            ctx.check_hostname = False
            log.info("Using existing CA cert for TLS verification during pairing")
            return ctx

        # TOFU: fetch CA cert from server before the exchange
        ca_pem = self._fetch_server_ca_cert(server_url)
        if ca_pem:
            try:
                # Load the PEM in-memory — no temp file needed
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.load_verify_locations(cadata=ca_pem)
                log.info(
                    "TOFU: verifying pairing exchange TLS using fetched server CA cert"
                )
                return ctx
            except ssl.SSLError as e:
                log.warning("Could not load fetched CA cert for TOFU: %s", e)

        # Last resort: no verification (same risk as before, now explicit fallback)
        log.warning(
            "Pairing exchange TLS is unverified — no CA cert available. "
            "Ensure you are on a trusted network before pairing."
        )
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

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
