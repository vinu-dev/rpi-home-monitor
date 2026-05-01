"""
Certificate management service (ADR-0009).

Monitors server certificate expiry and handles renewal.
Runs as a background thread, checking weekly.

Design patterns:
- Constructor Injection (certs_dir, audit)
- Single Responsibility (cert lifecycle only)
- Fail-Silent (cert check failure doesn't crash server)
"""

import logging
import os
import subprocess
import threading
import time
from datetime import UTC, datetime

log = logging.getLogger("monitor.cert-service")

# Check interval: weekly (in seconds)
CHECK_INTERVAL = 7 * 24 * 3600

# Warn when cert expires within this many days
EXPIRY_WARNING_DAYS = 30


# REQ: SWR-034; RISK: RISK-019; SEC: SC-017; TEST: TC-032
class CertService:
    """Monitors and renews server TLS certificates.

    Args:
        certs_dir: Path to certificate directory (e.g., /data/certs).
        audit: AuditLogger instance (optional).
    """

    def __init__(self, certs_dir, audit=None):
        self._certs_dir = certs_dir
        self._audit = audit
        self._thread = None
        self._running = False
        self._last_check = None
        self._expiry_date = None
        self._warning_logged = False

    @property
    def server_cert_path(self):
        return os.path.join(self._certs_dir, "server.crt")

    @property
    def server_key_path(self):
        return os.path.join(self._certs_dir, "server.key")

    @property
    def ca_cert_path(self):
        return os.path.join(self._certs_dir, "ca.crt")

    @property
    def ca_key_path(self):
        return os.path.join(self._certs_dir, "ca.key")

    @property
    def expiry_date(self):
        """Return the server cert expiry date (or None if unknown)."""
        return self._expiry_date

    @property
    def days_until_expiry(self):
        """Return days until server cert expires (or None if unknown)."""
        if self._expiry_date is None:
            return None
        delta = self._expiry_date - datetime.now(UTC)
        return max(0, delta.days)

    @property
    def needs_renewal(self):
        """Return True if cert is expired or within warning window."""
        days = self.days_until_expiry
        if days is None:
            return False
        return days <= EXPIRY_WARNING_DAYS

    def start(self):
        """Start background cert monitoring thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._check_loop, daemon=True, name="cert-check"
        )
        self._thread.start()
        log.info(
            "Certificate monitoring started (interval=%dd)", CHECK_INTERVAL // 86400
        )

    def stop(self):
        """Stop the monitoring thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def check_expiry(self):
        """Check the server certificate expiry date.

        Returns:
            (expiry_date, days_remaining, error) tuple.
        """
        cert_path = self.server_cert_path
        if not os.path.isfile(cert_path):
            return None, None, "Server certificate not found"

        try:
            result = subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-in",
                    cert_path,
                    "-noout",
                    "-enddate",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return None, None, f"openssl error: {result.stderr.strip()}"

            # Parse "notAfter=May 15 12:00:00 2031 GMT"
            line = result.stdout.strip()
            if "=" not in line:
                return None, None, f"Unexpected openssl output: {line}"

            date_str = line.split("=", 1)[1].strip()
            expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z")
            expiry = expiry.replace(tzinfo=UTC)

            self._expiry_date = expiry
            self._last_check = datetime.now(UTC)

            days = (expiry - datetime.now(UTC)).days
            return expiry, max(0, days), ""

        except subprocess.TimeoutExpired:
            return None, None, "openssl timed out"
        except (ValueError, OSError) as e:
            return None, None, str(e)

    def renew_server_cert(self):
        """Renew the server certificate using the CA key.

        Generates a new server cert with 5-year validity, signed by the CA.

        Returns:
            (success, error) tuple.
        """
        if not os.path.isfile(self.ca_key_path):
            return False, "CA key not found — cannot renew"
        if not os.path.isfile(self.ca_cert_path):
            return False, "CA cert not found — cannot renew"

        try:
            # Generate new server key
            key_path = self.server_key_path
            csr_path = os.path.join(self._certs_dir, "server.csr")

            # Generate ECDSA P-256 key
            result = subprocess.run(
                [
                    "openssl",
                    "ecparam",
                    "-genkey",
                    "-name",
                    "prime256v1",
                    "-noout",
                    "-out",
                    key_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return False, f"Key generation failed: {result.stderr.strip()}"

            # Generate CSR
            result = subprocess.run(
                [
                    "openssl",
                    "req",
                    "-new",
                    "-key",
                    key_path,
                    "-out",
                    csr_path,
                    "-subj",
                    "/CN=home-monitor-server",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return False, f"CSR generation failed: {result.stderr.strip()}"

            # Sign with CA (5-year validity = 1825 days)
            result = subprocess.run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-in",
                    csr_path,
                    "-CA",
                    self.ca_cert_path,
                    "-CAkey",
                    self.ca_key_path,
                    "-CAcreateserial",
                    "-out",
                    self.server_cert_path,
                    "-days",
                    "1825",
                    "-sha256",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return False, f"Signing failed: {result.stderr.strip()}"

            # Clean up CSR
            try:
                os.remove(csr_path)
            except OSError:
                pass

            # Set permissions
            os.chmod(key_path, 0o600)

            self._warning_logged = False
            log.info("Server certificate renewed (5-year validity)")
            self._log_audit("CERT_RENEWED", "", "", "Server certificate renewed")

            # Refresh expiry info
            self.check_expiry()
            return True, ""

        except (subprocess.TimeoutExpired, OSError) as e:
            return False, str(e)

    def get_cert_status(self):
        """Return certificate status for dashboard display.

        Returns:
            dict with cert status info.
        """
        expiry, days, err = self.check_expiry()
        if err:
            return {
                "status": "error",
                "error": err,
                "expiry_date": None,
                "days_remaining": None,
                "needs_renewal": False,
            }
        return {
            "status": "warning" if self.needs_renewal else "ok",
            "expiry_date": expiry.isoformat() if expiry else None,
            "days_remaining": days,
            "needs_renewal": self.needs_renewal,
        }

    def _check_loop(self):
        """Background loop: check cert expiry periodically."""
        # Initial check after 30s (let server finish starting)
        for _ in range(30):
            if not self._running:
                return
            time.sleep(1)

        while self._running:
            try:
                self._do_check()
            except Exception:
                log.exception("Certificate check failed")

            # Sleep in small increments for clean shutdown
            for _ in range(CHECK_INTERVAL):
                if not self._running:
                    return
                time.sleep(1)

    def _do_check(self):
        """Perform a single cert expiry check."""
        expiry, days, err = self.check_expiry()
        if err:
            log.warning("Cannot check cert expiry: %s", err)
            return

        log.info("Server cert expires %s (%d days remaining)", expiry, days)

        if days <= 0:
            log.error("Server certificate has EXPIRED — renewing")
            self._log_audit("CERT_EXPIRED", "", "", "Server certificate expired")
            ok, renew_err = self.renew_server_cert()
            if not ok:
                log.error("Auto-renewal failed: %s", renew_err)
        elif days <= EXPIRY_WARNING_DAYS and not self._warning_logged:
            log.warning(
                "Server certificate expires in %d days — renewal recommended", days
            )
            self._log_audit(
                "CERT_EXPIRY_WARNING",
                "",
                "",
                f"Server cert expires in {days} days",
            )
            self._warning_logged = True

    def _log_audit(self, event, user, ip, detail):
        """Log audit event (fail-silent)."""
        if self._audit:
            try:
                self._audit.log_event(event, user=user, ip=ip, detail=detail)
            except Exception:
                pass
