# REQ: SWR-064; RISK: RISK-021; SEC: SC-021; TEST: TC-042
"""Minimal localhost-only liveness route for the systemd watchdog probe."""

from flask import Blueprint, Response

healthz_bp = Blueprint("healthz", __name__)


@healthz_bp.get("/healthz")
def healthz():
    """Return the exact contract pinned by the watchdog probe tests."""
    return Response(b"ok\n", content_type="text/plain")
