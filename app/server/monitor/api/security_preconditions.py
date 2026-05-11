# REQ: SWR-101-A; RISK: RISK-101-1; SEC: SC-005, SC-101; TEST: TC-101-AC-12
"""Shared API guards for security-profile preconditions."""

from flask import current_app, jsonify


def require_secret_storage_allowed(feature: str):
    """Return a Flask response when encrypted-data policy blocks a secret write."""

    service = getattr(current_app, "data_protection_service", None)
    if service is None:
        return None
    allowed, payload = service.check_secret_write_allowed(feature)
    if allowed:
        return None
    return jsonify(payload), 428
