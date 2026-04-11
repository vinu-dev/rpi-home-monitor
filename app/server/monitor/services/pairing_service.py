"""
Camera pairing service — manages PIN-based pairing and certificate lifecycle.

Handles the server side of camera pairing (ADR-0009):
1. Admin initiates pairing → generates client cert + 6-digit PIN
2. Camera exchanges PIN for certs + pairing_secret
3. Admin unpairs → cert revoked, camera removed from trust

Design patterns:
- Constructor Injection (store, audit, certs_dir)
- Single Responsibility (pairing lifecycle only)
- Fail-Silent (audit failures don't break operations)
"""

import logging
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger("monitor.pairing_service")

PIN_EXPIRY_SECONDS = 300  # 5 minutes
PIN_MAX_ATTEMPTS = 3
PIN_DIGITS = 6


class PairingService:
    """Manages camera pairing: cert generation, PIN exchange, and revocation.

    Args:
        store: Data persistence layer (Store instance).
        audit: Security audit logger (AuditLogger instance or None).
        certs_dir: Path to /data/certs/ directory.
    """

    def __init__(self, store, audit=None, certs_dir="/data/certs"):
        self._store = store
        self._audit = audit
        self._certs_dir = Path(certs_dir)
        self._pending_pairings = {}  # camera_id -> {pin, expires_at, attempts, cert_data}
        self._revoked_serials = set()
        self._load_revoked_serials()

    def _load_revoked_serials(self):
        """Rebuild revocation set from cameras/revoked/ on startup."""
        revoked_dir = self._certs_dir / "cameras" / "revoked"
        if not revoked_dir.is_dir():
            return
        for cert_file in revoked_dir.glob("*.crt"):
            try:
                result = subprocess.run(
                    ["openssl", "x509", "-in", str(cert_file), "-serial", "-noout"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    serial = result.stdout.strip().split("=")[-1]
                    self._revoked_serials.add(serial)
            except (subprocess.TimeoutExpired, OSError):
                log.warning("Failed to read serial from revoked cert: %s", cert_file)

    def initiate_pairing(self, camera_id, user="", ip=""):
        """Generate client cert and PIN for a camera.

        Returns (pin, error, status_code).
        """
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return None, "Camera not found", 404

        if camera.status not in ("pending", "offline"):
            return None, "Camera must be in pending or offline state to pair", 400

        # Generate client cert
        cert_data, error = self._generate_client_cert(camera_id)
        if error:
            return None, f"Certificate generation failed: {error}", 500

        # Generate PIN
        pin = str(secrets.randbelow(10**PIN_DIGITS)).zfill(PIN_DIGITS)

        self._pending_pairings[camera_id] = {
            "pin": pin,
            "expires_at": time.time() + PIN_EXPIRY_SECONDS,
            "attempts": 0,
            "cert_data": cert_data,
        }

        self._log_audit(
            "PAIRING_INITIATED",
            user,
            ip,
            f"pairing initiated for camera {camera_id}",
        )

        return pin, "", 200

    def exchange_certs(self, pin, camera_id):
        """Validate PIN and return certs + pairing_secret.

        Returns (result_dict, error, status_code).
        """
        pending = self._pending_pairings.get(camera_id)
        if pending is None:
            return None, "No pairing in progress for this camera", 404

        # Check expiry
        if time.time() > pending["expires_at"]:
            del self._pending_pairings[camera_id]
            return None, "Pairing PIN has expired", 410

        # Rate limiting
        pending["attempts"] += 1
        if pending["attempts"] > PIN_MAX_ATTEMPTS:
            del self._pending_pairings[camera_id]
            self._log_audit(
                "PAIRING_FAILED",
                "",
                "",
                f"too many PIN attempts for camera {camera_id}",
            )
            return None, "Too many attempts, pairing cancelled", 429

        # Validate PIN
        if not secrets.compare_digest(pin, pending["pin"]):
            remaining = PIN_MAX_ATTEMPTS - pending["attempts"]
            self._log_audit(
                "PAIRING_FAILED",
                "",
                "",
                f"invalid PIN for camera {camera_id}, {remaining} attempts remaining",
            )
            return None, f"Invalid PIN, {remaining} attempts remaining", 403

        # PIN valid — generate pairing_secret and finalize
        cert_data = pending["cert_data"]
        pairing_secret = os.urandom(32).hex()

        # Read CA cert for the response
        ca_cert = self._read_file(self._certs_dir / "ca.crt")
        if ca_cert is None:
            return None, "CA certificate not found", 500

        # Update camera in store
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return None, "Camera not found", 404

        camera.status = "online"
        camera.cert_serial = cert_data["serial"]
        camera.pairing_secret = pairing_secret
        self._store.save_camera(camera)

        # Clean up pending state
        del self._pending_pairings[camera_id]

        self._log_audit(
            "CAMERA_PAIRED",
            "",
            "",
            f"camera {camera_id} paired successfully, cert serial {cert_data['serial']}",
        )

        return (
            {
                "client_cert": cert_data["cert"],
                "client_key": cert_data["key"],
                "ca_cert": ca_cert,
                "pairing_secret": pairing_secret,
                "rtsps_url": f"rtsps://home-monitor.local:8554/{camera_id}",
            },
            "",
            200,
        )

    def unpair(self, camera_id, user="", ip=""):
        """Revoke camera cert and reset pairing state.

        Returns (error, status_code).
        """
        camera = self._store.get_camera(camera_id)
        if camera is None:
            return "Camera not found", 404

        # Move cert to revoked
        cert_path = self._certs_dir / "cameras" / f"{camera_id}.crt"
        revoked_dir = self._certs_dir / "cameras" / "revoked"
        revoked_dir.mkdir(parents=True, exist_ok=True)

        if cert_path.exists():
            revoked_path = revoked_dir / f"{camera_id}.crt"
            shutil.move(str(cert_path), str(revoked_path))
            if camera.cert_serial:
                self._revoked_serials.add(camera.cert_serial)

        # Remove key file
        key_path = self._certs_dir / "cameras" / f"{camera_id}.key"
        if key_path.exists():
            key_path.unlink()

        # Update camera state
        camera.status = "pending"
        camera.cert_serial = ""
        camera.pairing_secret = ""
        self._store.save_camera(camera)

        # Cancel any pending pairing
        self._pending_pairings.pop(camera_id, None)

        self._log_audit(
            "CERT_REVOKED",
            user,
            ip,
            f"cert revoked for camera {camera_id}",
        )

        return "", 200

    def is_cert_revoked(self, serial):
        """Check if a certificate serial is in the revocation set."""
        return serial in self._revoked_serials

    def get_pending_pairing(self, camera_id):
        """Get pending pairing info (for dashboard display). Returns None if none."""
        pending = self._pending_pairings.get(camera_id)
        if pending is None:
            return None
        if time.time() > pending["expires_at"]:
            del self._pending_pairings[camera_id]
            return None
        return {
            "pin": pending["pin"],
            "expires_in": int(pending["expires_at"] - time.time()),
            "attempts": pending["attempts"],
        }

    def _generate_client_cert(self, camera_id):
        """Generate ECDSA P-256 client cert signed by the CA.

        Returns (cert_data_dict, error_string).
        cert_data_dict has keys: cert, key, serial, cert_path, key_path.
        """
        cameras_dir = self._certs_dir / "cameras"
        cameras_dir.mkdir(parents=True, exist_ok=True)

        ca_key = self._certs_dir / "ca.key"
        ca_cert = self._certs_dir / "ca.crt"
        client_key = cameras_dir / f"{camera_id}.key"
        client_cert = cameras_dir / f"{camera_id}.crt"
        client_csr = cameras_dir / f"{camera_id}.csr"

        if not ca_key.exists() or not ca_cert.exists():
            return None, "CA key or certificate not found"

        try:
            # Generate client private key
            self._run_openssl(
                [
                    "openssl",
                    "ecparam",
                    "-genkey",
                    "-name",
                    "prime256v1",
                    "-out",
                    str(client_key),
                ]
            )
            os.chmod(str(client_key), 0o600)

            # Generate CSR
            self._run_openssl(
                [
                    "openssl",
                    "req",
                    "-new",
                    "-key",
                    str(client_key),
                    "-out",
                    str(client_csr),
                    "-subj",
                    f"/CN={camera_id}/O=HomeMonitor",
                ]
            )

            # Sign with CA (5 years = 1825 days)
            self._run_openssl(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-in",
                    str(client_csr),
                    "-CA",
                    str(ca_cert),
                    "-CAkey",
                    str(ca_key),
                    "-CAcreateserial",
                    "-out",
                    str(client_cert),
                    "-days",
                    "1825",
                ]
            )

            # Read serial
            result = subprocess.run(
                ["openssl", "x509", "-in", str(client_cert), "-serial", "-noout"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            serial = (
                result.stdout.strip().split("=")[-1] if result.returncode == 0 else ""
            )

            # Read cert and key content
            cert_content = self._read_file(client_cert)
            key_content = self._read_file(client_key)

            if cert_content is None or key_content is None:
                return None, "Failed to read generated certificate files"

            return {
                "cert": cert_content,
                "key": key_content,
                "serial": serial,
                "cert_path": str(client_cert),
                "key_path": str(client_key),
            }, ""

        except subprocess.CalledProcessError as e:
            log.error("OpenSSL command failed: %s", e.stderr)
            return None, f"OpenSSL error: {e.stderr}"
        except OSError as e:
            log.error("File operation failed: %s", e)
            return None, str(e)
        finally:
            # Clean up CSR
            if client_csr.exists():
                client_csr.unlink()

    def _run_openssl(self, cmd):
        """Run an openssl command, raising on failure."""
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result

    def _read_file(self, path):
        """Read a file's text content. Returns None on failure."""
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return None

    def _log_audit(self, event, user, ip, detail):
        """Log audit event, swallowing errors."""
        if not self._audit:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception as e:
            log.warning("Audit log failed: %s", e)
