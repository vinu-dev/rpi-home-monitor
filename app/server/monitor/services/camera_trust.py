# REQ: SWR-039, SWR-065; RISK: RISK-007, RISK-015; SEC: SC-002, SC-012; TEST: TC-037, TC-054
"""
Helpers for pinning and persisting camera HTTPS status certificates.

The server stores the camera's self-signed control-plane certificate on
disk and persists its SHA-256 fingerprint in the camera record. Keeping
the PEM and the fingerprint separate lets the control client:

- run normal TLS verification when it already has the pinned PEM, and
- recover from a missing PEM file by comparing the live peer cert against
  the stored fingerprint before re-persisting it.
"""

from __future__ import annotations

import hashlib
import ssl
from pathlib import Path

PINNED_STATUS_CERT_DIRNAME = "status"


def pinned_status_cert_path(certs_dir: str, camera_id: str) -> Path:
    """Return the path used for a camera's pinned status certificate."""
    return Path(certs_dir) / PINNED_STATUS_CERT_DIRNAME / f"{camera_id}.crt"


def normalize_status_cert_pem(cert_pem: str) -> str:
    """Validate and normalize a PEM certificate string."""
    normalized = (cert_pem or "").strip()
    if not normalized:
        raise ValueError("camera status certificate is empty")
    # Raises ValueError if the PEM is malformed.
    ssl.PEM_cert_to_DER_cert(normalized + "\n")
    return normalized + "\n"


def status_cert_fingerprint_from_der(cert_der: bytes) -> str:
    """Return the lowercase SHA-256 fingerprint for a DER certificate."""
    return hashlib.sha256(cert_der).hexdigest()


def status_cert_fingerprint_from_pem(cert_pem: str) -> str:
    """Return the lowercase SHA-256 fingerprint for a PEM certificate."""
    der = ssl.PEM_cert_to_DER_cert(normalize_status_cert_pem(cert_pem))
    return status_cert_fingerprint_from_der(der)


def load_pinned_status_cert(certs_dir: str, camera_id: str) -> str:
    """Read a pinned status cert PEM from disk. Missing file -> empty string."""
    path = pinned_status_cert_path(certs_dir, camera_id)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def persist_pinned_status_cert(certs_dir: str, camera_id: str, cert_pem: str) -> Path:
    """Persist a normalized pinned status cert PEM to disk."""
    path = pinned_status_cert_path(certs_dir, camera_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalize_status_cert_pem(cert_pem), encoding="utf-8")
    return path


def remove_pinned_status_cert(certs_dir: str, camera_id: str) -> None:
    """Remove a pinned status cert from disk if it exists."""
    try:
        pinned_status_cert_path(certs_dir, camera_id).unlink()
    except FileNotFoundError:
        return
