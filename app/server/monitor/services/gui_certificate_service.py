"""GUI HTTPS certificate CSR and install service.

The camera/MediaMTX certificate remains ``server.crt``/``server.key``.
This service manages the browser-facing nginx certificate only:
``gui-server.crt``/``gui-server.key``.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from monitor.services import privileged

log = logging.getLogger("monitor.services.gui_certificate_service")


@dataclass(frozen=True)
class GuiCertificateStatus:
    installed: bool
    fallback_active: bool
    certificate_path: str
    key_path: str
    csr_path: str
    hostname: str
    machine_id: str
    suggested_sans: list[str]
    fingerprint: str = ""
    subject: str = ""
    issuer: str = ""
    not_valid_after: str = ""


class GuiCertificateService:
    """Manage browser-facing HTTPS certificate material."""

    def __init__(
        self,
        *,
        certs_dir: str,
        trust_ca_path: str = "",
        audit=None,
        nginx_reload: bool = True,
    ):
        self._certs_dir = Path(certs_dir)
        self._trust_ca_path = Path(trust_ca_path) if trust_ca_path else None
        self._audit = audit
        self._nginx_reload = nginx_reload

    @property
    def gui_cert_path(self) -> Path:
        return self._certs_dir / "gui-server.crt"

    @property
    def gui_key_path(self) -> Path:
        return self._certs_dir / "gui-server.key"

    @property
    def gui_csr_path(self) -> Path:
        return self._certs_dir / "gui-server.csr"

    @property
    def camera_server_cert_path(self) -> Path:
        return self._certs_dir / "server.crt"

    @property
    def camera_server_key_path(self) -> Path:
        return self._certs_dir / "server.key"

    def ensure_fallback(self) -> None:
        """Populate GUI cert files from the existing server cert when missing."""
        self._certs_dir.mkdir(parents=True, exist_ok=True)
        if not self.gui_cert_path.exists() and self.camera_server_cert_path.exists():
            shutil.copy2(self.camera_server_cert_path, self.gui_cert_path)
            self.gui_cert_path.chmod(0o644)
        if not self.gui_key_path.exists() and self.camera_server_key_path.exists():
            shutil.copy2(self.camera_server_key_path, self.gui_key_path)
            self.gui_key_path.chmod(0o600)

    def status(self) -> GuiCertificateStatus:
        self.ensure_fallback()
        cert = (
            _load_first_cert(self.gui_cert_path.read_bytes())
            if self.gui_cert_path.exists()
            else None
        )
        fingerprint = ""
        subject = ""
        issuer = ""
        not_valid_after = ""
        if cert is not None:
            fingerprint = _fingerprint(cert)
            subject = cert.subject.rfc4514_string()
            issuer = cert.issuer.rfc4514_string()
            not_valid_after = cert.not_valid_after_utc.isoformat()
        return GuiCertificateStatus(
            installed=self.gui_cert_path.exists() and self.gui_key_path.exists(),
            fallback_active=self._is_fallback_active(),
            certificate_path=str(self.gui_cert_path),
            key_path=str(self.gui_key_path),
            csr_path=str(self.gui_csr_path),
            hostname=_hostname(),
            machine_id=_machine_id(),
            suggested_sans=self.suggested_sans(),
            fingerprint=fingerprint,
            subject=subject,
            issuer=issuer,
            not_valid_after=not_valid_after,
        )

    def suggested_sans(self) -> list[str]:
        hostname = _hostname()
        names = [
            hostname,
            f"{hostname}.local" if not hostname.endswith(".local") else hostname,
            "home-monitor.local",
            "homemonitor.local",
            "192.168.4.1",
            "127.0.0.1",
        ]
        names.extend(_local_ip_addresses())
        seen: set[str] = set()
        result: list[str] = []
        for name in names:
            if name and name not in seen:
                seen.add(name)
                result.append(name)
        return result

    def create_csr(
        self,
        *,
        common_name: str = "",
        sans: list[str] | None = None,
        rotate_key: bool = False,
    ) -> tuple[str, str]:
        self._certs_dir.mkdir(parents=True, exist_ok=True)
        common_name = (common_name or _hostname() or "home-monitor.local").strip()
        san_values = _normalise_sans(sans or self.suggested_sans(), common_name)
        if rotate_key or not self.gui_key_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            _atomic_write(
                self.gui_key_path,
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
                mode=0o600,
            )
        else:
            key = serialization.load_pem_private_key(
                self.gui_key_path.read_bytes(),
                password=None,
            )

        csr = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(
                x509.Name(
                    [
                        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                        x509.NameAttribute(
                            NameOID.ORGANIZATION_NAME,
                            "Home Monitor Local Provisioning",
                        ),
                    ]
                )
            )
            .add_extension(
                x509.SubjectAlternativeName(_general_names(san_values)),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        pem = csr.public_bytes(serialization.Encoding.PEM)
        _atomic_write(self.gui_csr_path, pem, mode=0o644)
        return pem.decode("ascii"), self.gui_csr_path.name

    def install_certificate(self, certificate_pem: str) -> tuple[bool, str]:
        pem = str(certificate_pem or "").strip().encode("utf-8")
        if not pem:
            return False, "certificate PEM is required"
        if not self.gui_key_path.exists():
            return False, "GUI server private key is missing; create a CSR first"
        try:
            cert = _load_first_cert(pem)
            if cert is None:
                return False, "certificate PEM could not be parsed"
            key = serialization.load_pem_private_key(
                self.gui_key_path.read_bytes(),
                password=None,
            )
        except (OSError, ValueError, TypeError) as exc:
            return False, f"certificate material could not be read: {exc}"

        error = self._validate_server_certificate(cert, key)
        if error:
            return False, error

        _atomic_write(self.gui_cert_path, pem + b"\n", mode=0o644)
        try:
            self.gui_csr_path.unlink()
        except FileNotFoundError:
            pass
        self._log("GUI_CERT_INSTALLED", detail=f"fingerprint={_fingerprint(cert)}")

        if self._nginx_reload:
            ok, error = self.reload_nginx()
            if not ok:
                return False, f"certificate installed but nginx reload failed: {error}"
        return True, ""

    def reload_nginx(self) -> tuple[bool, str]:
        helper_disabled = os.environ.get("MONITOR_DISABLE_PRIVILEGED_HELPER") == "1"
        helper_required = (
            os.name == "posix"
            and not helper_disabled
            and (
                privileged.should_use_helper()
                or _is_unprivileged_process()
                or privileged.is_helper_available()
            )
        )
        if helper_required:
            try:
                privileged.request("nginx.reload", timeout=20)
                return True, ""
            except privileged.PrivilegedHelperError as exc:
                return False, str(exc)
        try:
            test = subprocess.run(
                ["nginx", "-t"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if test.returncode != 0:
                return False, test.stderr.strip() or test.stdout.strip()
            reload_result = subprocess.run(
                ["nginx", "-s", "reload"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if reload_result.returncode != 0:
                return (
                    False,
                    reload_result.stderr.strip() or reload_result.stdout.strip(),
                )
            return True, ""
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)

    def _validate_server_certificate(self, cert, key) -> str:
        if cert.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ) != key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ):
            return "certificate does not match the RPI-generated GUI private key"

        try:
            eku = cert.extensions.get_extension_for_oid(
                ExtensionOID.EXTENDED_KEY_USAGE
            ).value
        except x509.ExtensionNotFound:
            return "certificate is missing extended key usage"
        if ExtendedKeyUsageOID.SERVER_AUTH not in eku:
            return "certificate is not valid for HTTPS server authentication"

        try:
            san = cert.extensions.get_extension_for_oid(
                ExtensionOID.SUBJECT_ALTERNATIVE_NAME
            ).value
        except x509.ExtensionNotFound:
            return "certificate is missing subject alternative names"
        if not list(san):
            return "certificate must include at least one DNS or IP SAN"

        now = datetime.now(UTC)
        if now < cert.not_valid_before_utc:
            return "certificate is not valid yet"
        if now > cert.not_valid_after_utc:
            return "certificate has expired"

        ca_cert, error = self._trusted_ca()
        if error:
            return error
        return _verify_issued_by_trusted_ca(cert, ca_cert)

    def _trusted_ca(self):
        if self._trust_ca_path is None:
            return None, "trusted local CA certificate path is not configured"
        try:
            return x509.load_pem_x509_certificate(self._trust_ca_path.read_bytes()), ""
        except FileNotFoundError:
            return None, "trusted local CA certificate is not installed on this RPI"
        except (OSError, ValueError):
            return None, "trusted local CA certificate could not be parsed"

    def _is_fallback_active(self) -> bool:
        try:
            if (
                not self.gui_cert_path.exists()
                or not self.camera_server_cert_path.exists()
            ):
                return False
            return (
                self.gui_cert_path.read_bytes()
                == self.camera_server_cert_path.read_bytes()
            )
        except OSError:
            return False

    def _log(self, event: str, *, detail: str = "") -> None:
        if self._audit is None:
            return
        try:
            self._audit.log_event(event, detail=detail)
        except Exception:  # pragma: no cover - defensive
            log.debug("Audit log failed for %s", event, exc_info=True)


def _atomic_write(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _is_unprivileged_process() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() != 0


def _load_first_cert(pem: bytes) -> x509.Certificate | None:
    marker = b"-----END CERTIFICATE-----"
    end = pem.find(marker)
    if end < 0:
        return None
    first = pem[: end + len(marker)] + b"\n"
    return x509.load_pem_x509_certificate(first)


def _verify_issued_by_trusted_ca(cert, ca_cert) -> str:
    if cert.issuer != ca_cert.subject:
        return "certificate was not issued by the trusted local CA"
    try:
        basic = ca_cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        return "trusted local CA certificate is missing CA constraints"
    if not basic.ca:
        return "trusted local CA certificate is not a CA"

    public_key = ca_cert.public_key()
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                padding.PKCS1v15(),
                cert.signature_hash_algorithm,
            )
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                ec.ECDSA(cert.signature_hash_algorithm),
            )
        else:
            return "trusted local CA key type is unsupported"
    except Exception:
        return "certificate signature does not match the trusted local CA"
    return ""


def _fingerprint(cert) -> str:
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return "SHA256:" + ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def _hostname() -> str:
    return (socket.gethostname() or "home-monitor").split(".", 1)[0]


def _machine_id() -> str:
    for path in (
        Path("/etc/machine-id"),
        Path("/var/lib/dbus/machine-id"),
        Path("/proc/device-tree/serial-number"),
        Path("/sys/firmware/devicetree/base/serial-number"),
    ):
        try:
            value = path.read_text(encoding="utf-8", errors="ignore").strip("\x00\n ")
        except OSError:
            continue
        if value:
            return value
    return ""


def _local_ip_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                addresses.add(ip)
    except OSError:
        pass
    return sorted(addresses)


def _normalise_sans(values: list[str], common_name: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in [common_name, *values]:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        _general_name(value)
        seen.add(value)
        result.append(value)
    if not result:
        raise ValueError("at least one SAN is required")
    return result


def _general_names(values: list[str]) -> list[x509.GeneralName]:
    return [_general_name(value) for value in values]


def _general_name(value: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(value))
    except ValueError as exc:
        if len(value) > 253 or any(ch.isspace() for ch in value):
            raise ValueError(f"invalid DNS SAN: {value}") from exc
        return x509.DNSName(value)
