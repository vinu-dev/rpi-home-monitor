# REQ: SWR-064; RISK: RISK-021; SEC: SC-021; TEST: TC-042
"""Minimal localhost-only liveness route for the systemd watchdog probe."""

from flask import Blueprint, Response, request

healthz_bp = Blueprint("healthz", __name__)

_ALLOWED_REMOTES = {"127.0.0.1", "::1"}


@healthz_bp.before_request
def _require_loopback():
    """Reject requests that did not originate from the local probe."""
    if (request.remote_addr or "") not in _ALLOWED_REMOTES:
        return Response(b"forbidden\n", status=403, content_type="text/plain")
    return None


@healthz_bp.get("/healthz")
def healthz():
    """Return the exact contract pinned by the watchdog probe tests."""
    return Response(b"ok\n", content_type="text/plain")
