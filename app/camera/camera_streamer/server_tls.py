# REQ: SWR-003, SWR-004; RISK: RISK-002, RISK-005; SEC: SC-002; TEST: TC-012
"""TLS helpers for camera-to-server HTTPS clients."""

from __future__ import annotations

import hashlib
import os
import ssl


def format_fingerprint(hex_digest: str) -> str:
    digest = "".join(str(hex_digest or "").replace(":", "").split()).lower()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)).upper()


def normalize_fingerprint(value: str) -> str:
    return "".join(str(value or "").replace(":", "").split()).lower()


def ca_fingerprint_from_pem(ca_pem: str) -> str:
    """Return a display-safe SHA-256 fingerprint for a CA PEM."""
    pem = str(ca_pem or "").strip()
    try:
        material = ssl.PEM_cert_to_DER_cert(pem)
    except (ssl.SSLError, ValueError):
        material = pem.encode("utf-8")
    return format_fingerprint(hashlib.sha256(material).hexdigest())


def ca_fingerprint_from_file(ca_path: str) -> str:
    with open(ca_path, encoding="utf-8") as handle:
        return ca_fingerprint_from_pem(handle.read())


def context_from_ca_pem(ca_pem: str) -> ssl.SSLContext:
    """Build a server-verifying context from a fetched CA PEM."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=ca_pem)
    return ctx


def paired_server_context(
    certs_dir: str,
    *,
    include_client_cert: bool = True,
) -> ssl.SSLContext:
    """Build a verified TLS context using the paired server CA.

    Hostname checking stays disabled because cameras commonly connect by
    current LAN IP while the server certificate is issued for its local name.
    The trust decision is still pinned to the paired CA.
    """
    ca_path = os.path.join(certs_dir, "ca.crt")
    if not os.path.isfile(ca_path):
        raise FileNotFoundError(f"paired server CA certificate not found: {ca_path}")

    ctx = ssl.create_default_context(cafile=ca_path)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED

    if include_client_cert:
        cert = os.path.join(certs_dir, "client.crt")
        key = os.path.join(certs_dir, "client.key")
        if os.path.isfile(cert) and os.path.isfile(key):
            ctx.load_cert_chain(cert, key)

    return ctx
