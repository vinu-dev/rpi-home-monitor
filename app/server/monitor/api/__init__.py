# REQ: SWR-001, SWR-045, SWR-064; RISK: RISK-002, RISK-021; SEC: SC-001, SC-021; TEST: TC-004, TC-042
"""API blueprints for the monitoring server REST API (v1)."""

from monitor.api.healthz import healthz_bp

__all__ = ["healthz_bp"]
