# REQ: SWR-001, SWR-034; RISK: RISK-002, RISK-019; SEC: SC-001, SC-017; TEST: TC-004, TC-032
"""Client certificate authentication service.

This is the application-layer half of the local-CA mTLS design. nginx performs
TLS-chain verification and forwards certificate metadata; this service parses
the client certificate, enforces product profile rules, and maps the certificate
to an effective local role.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

log = logging.getLogger("monitor.services.certificate_auth_service")

VERIFY_HEADER = "X-Client-Cert-Verify"
CERT_HEADER = "X-Client-Cert"
FINGERPRINT_HEADER = "X-Client-Cert-Fingerprint"
SERIAL_HEADER = "X-Client-Cert-Serial"

PROFILE_URI_PREFIX = "urn:home-monitor:profile:"
VALID_PROFILES = frozenset(
    {
        "owner-admin",
        "viewer",
        "setup-provisioner",
        "service-maintenance",
        "read-only-support",
        "backend-registrar",
    }
)
PROFILE_TO_ROLE = {
    "owner-admin": "admin",
    "viewer": "viewer",
    "read-only-support": "viewer",
    # Service profiles are intentionally conservative in this first slice.
    # Route-level action policy can elevate specific service actions later.
    "setup-provisioner": "viewer",
    "service-maintenance": "viewer",
    "backend-registrar": "viewer",
}


@dataclass(frozen=True)
class CertificatePrincipal:
    """Authenticated certificate identity mapped to a local session user."""

    user_id: str
    username: str
    role: str
    profile: str
    fingerprint: str
    serial: str
    subject: str
    issuer: str
    display_name: str = ""


class CertificateAuthService:
    """Validate nginx-forwarded client certificate identity."""

    def __init__(
        self,
        *,
        config_dir: str,
        audit=None,
        allow_profile_login: bool = False,
        enforce_time: bool = True,
    ):
        self._config_dir = Path(config_dir)
        self._audit = audit
        self._allow_profile_login = bool(allow_profile_login)
        self._enforce_time = bool(enforce_time)

    @property
    def cert_users_path(self) -> Path:
        return self._config_dir / "cert-users.json"

    @property
    def denylist_path(self) -> Path:
        return self._config_dir / "cert-denylist.json"

    def authenticate_headers(self, headers) -> tuple[CertificatePrincipal | None, str]:
        """Return an authenticated principal or a denial reason."""
        verify = str(headers.get(VERIFY_HEADER, "") or "").strip().upper()
        if verify != "SUCCESS":
            return None, "client certificate was not verified"

        raw_pem = str(headers.get(CERT_HEADER, "") or "").strip()
        if not raw_pem:
            return None, "client certificate header missing"

        cert, error = _load_certificate(raw_pem)
        if error:
            return None, error

        fingerprint = _fingerprint(cert)
        serial = _serial(cert)
        if not _header_matches(headers.get(FINGERPRINT_HEADER, ""), fingerprint):
            return None, "client certificate fingerprint mismatch"
        if not _header_matches(headers.get(SERIAL_HEADER, ""), serial):
            return None, "client certificate serial mismatch"

        if self._is_denied(fingerprint, serial):
            return None, "client certificate is denied"

        if not _has_client_auth_eku(cert):
            return None, "client certificate is not valid for client authentication"

        if self._enforce_time:
            now = datetime.now(UTC)
            if now < cert.not_valid_before_utc:
                return None, "client certificate is not valid yet"
            if now > cert.not_valid_after_utc:
                return None, "client certificate has expired"

        profile = _profile_from_certificate(cert)
        if profile not in VALID_PROFILES:
            return None, "client certificate profile is missing or unsupported"

        subject = _name_string(cert.subject)
        issuer = _name_string(cert.issuer)
        local_record = self._local_record(fingerprint, serial)
        if local_record is None and not self._allow_profile_login:
            return None, "client certificate is not locally allowed"

        display_name = ""
        role = PROFILE_TO_ROLE[profile]
        user_id = f"cert-{_compact_fingerprint(fingerprint)[:16].lower()}"
        username = _common_name(cert) or user_id
        if local_record is not None:
            display_name = str(local_record.get("display_name") or "").strip()
            username = display_name or username
            user_id = str(local_record.get("id") or user_id)
            local_role = str(local_record.get("role") or "").strip()
            if local_role:
                role = _least_privilege_role(role, local_role)

        return (
            CertificatePrincipal(
                user_id=user_id,
                username=username,
                role=role,
                profile=profile,
                fingerprint=fingerprint,
                serial=serial,
                subject=subject,
                issuer=issuer,
                display_name=display_name,
            ),
            "",
        )

    def log_success(self, principal: CertificatePrincipal, *, ip: str = "") -> None:
        self._log(
            "CERT_AUTH_SUCCESS",
            user=principal.username,
            ip=ip,
            detail=(
                f"profile={principal.profile} role={principal.role} "
                f"serial={principal.serial} fingerprint={principal.fingerprint}"
            ),
        )

    def log_denial(self, reason: str, *, ip: str = "") -> None:
        self._log("CERT_AUTH_DENIED", ip=ip, detail=reason)

    def _local_record(self, fingerprint: str, serial: str) -> dict | None:
        data = _read_json_object(self.cert_users_path)
        users = data.get("users") if isinstance(data, dict) else None
        if not isinstance(users, list):
            return None
        for user in users:
            if not isinstance(user, dict):
                continue
            if not bool(user.get("enabled", True)):
                continue
            if _normalise_fingerprint(user.get("cert_fingerprint", "")) == fingerprint:
                return user
            if str(user.get("cert_serial") or "").upper() == serial:
                return user
        return None

    def _is_denied(self, fingerprint: str, serial: str) -> bool:
        data = _read_json_object(self.denylist_path)
        fingerprints = data.get("fingerprints") if isinstance(data, dict) else None
        serials = data.get("serials") if isinstance(data, dict) else None
        if isinstance(fingerprints, list) and fingerprint in {
            _normalise_fingerprint(item) for item in fingerprints
        }:
            return True
        return isinstance(serials, list) and serial in {
            str(item or "").upper() for item in serials
        }

    def _log(self, event: str, *, user: str = "", ip: str = "", detail: str = ""):
        if self._audit is None:
            return
        try:
            self._audit.log_event(event, user=user, ip=ip, detail=detail)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("Audit log failed for %s: %s", event, exc)


def _load_certificate(raw_pem: str) -> tuple[x509.Certificate | None, str]:
    pem = unquote(raw_pem).replace("\\n", "\n").strip()
    if "BEGIN CERTIFICATE" not in pem:
        return None, "client certificate header is not PEM"
    try:
        cert = x509.load_pem_x509_certificate(pem.encode("utf-8"))
    except ValueError:
        return None, "client certificate PEM could not be parsed"
    return cert, ""


def _fingerprint(cert: x509.Certificate) -> str:
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return "SHA256:" + ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def _serial(cert: x509.Certificate) -> str:
    return f"{cert.serial_number:X}"


def _header_matches(header_value, expected: str) -> bool:
    raw = str(header_value or "").strip()
    if not raw:
        return True
    if expected.startswith("SHA256:"):
        compact = raw.upper().removeprefix("SHA256").replace("=", "")
        compact = compact.replace(":", "").replace(" ", "")
        if len(compact) != 64:
            return True
        return _normalise_fingerprint(raw) == expected
    return raw.upper() == expected


def _normalise_fingerprint(value) -> str:
    raw = str(value or "").strip().upper()
    raw = raw.removeprefix("SHA256")
    raw = raw.replace("=", "").replace(":", "").replace(" ", "")
    if not raw:
        return ""
    return "SHA256:" + ":".join(raw[i : i + 2] for i in range(0, len(raw), 2))


def _compact_fingerprint(value: str) -> str:
    return _normalise_fingerprint(value).removeprefix("SHA256:").replace(":", "")


def _has_client_auth_eku(cert: x509.Certificate) -> bool:
    try:
        eku = cert.extensions.get_extension_for_oid(
            ExtensionOID.EXTENDED_KEY_USAGE
        ).value
    except x509.ExtensionNotFound:
        return False
    return ExtendedKeyUsageOID.CLIENT_AUTH in eku


def _profile_from_certificate(cert: x509.Certificate) -> str:
    try:
        san = cert.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value
    except x509.ExtensionNotFound:
        return ""
    for uri in san.get_values_for_type(x509.UniformResourceIdentifier):
        if uri.startswith(PROFILE_URI_PREFIX):
            return uri[len(PROFILE_URI_PREFIX) :].strip()
    return ""


def _common_name(cert: x509.Certificate) -> str:
    values = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not values:
        return ""
    return values[0].value.strip()


def _name_string(name: x509.Name) -> str:
    return name.rfc4514_string()


def _least_privilege_role(cert_role: str, local_role: str) -> str:
    if cert_role != "admin" or local_role != "admin":
        return "viewer"
    return "admin"


def _read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def public_certificate_pem(cert: x509.Certificate) -> str:
    """Return a PEM string for tests and tooling."""
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
