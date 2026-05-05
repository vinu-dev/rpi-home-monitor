# REQ: SWR-052; RISK: RISK-001, RISK-017; SEC: SC-016; TEST: TC-001, TC-028
"""
On-demand coordinator (ADR-0017) — localhost-only Flask blueprint.

MediaMTX invokes this via a shell wrapper on `runOnDemand` /
`runOnDemandCloseAfter`. The coordinator is the single "does anyone still
need this stream?" gate:

    POST /internal/on-demand/<camera_id>/start
        - If camera.desired_stream_state == "running" → no-op (200).
        - Else → control_client.start_stream(ip), persist, return {"ok": true}.

    POST /internal/on-demand/<camera_id>/stop
        - If RecordingScheduler.needs_stream(cam) → no-op (200, kept_running).
        - Else → control_client.stop_stream(ip), persist, return {"ok": true}.

Auth: 127.0.0.1 / ::1 only (localhost trust). No session / CSRF.
CSRF is not applied (blueprint is not under /api/v1).
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

log = logging.getLogger("monitor.on_demand")

on_demand_bp = Blueprint("on_demand", __name__)

_ALLOWED_REMOTES = {"127.0.0.1", "::1"}


@on_demand_bp.before_request
def _require_localhost():
    """Reject any request that didn't come from the loopback interface."""
    # request.remote_addr may include IPv6-mapped IPv4 or similar; we keep
    # the check strict because MediaMTX is local.
    addr = request.remote_addr or ""
    if addr not in _ALLOWED_REMOTES:
        return jsonify({"error": "Forbidden"}), 403
    return None


@on_demand_bp.route("/<camera_id>/start", methods=["POST"])
def on_demand_start(camera_id: str):
    """Viewer arrived — make sure the camera is streaming."""
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    if camera.desired_stream_state == "running":
        log.debug("on-demand start %s: already running", camera_id)
        return jsonify({"ok": True, "already_running": True}), 200

    if not camera.ip:
        return jsonify({"error": "Camera IP unknown"}), 409

    control = getattr(current_app, "camera_control_client", None)
    if control is None:
        return jsonify({"error": "Control client unavailable"}), 503

    _, err = control.start_stream(camera.ip, camera_id=camera.id)
    if err:
        log.warning("on-demand start %s failed: %s", camera_id, err)
        return jsonify({"error": err}), 502

    camera.desired_stream_state = "running"
    current_app.store.save_camera(camera)
    return jsonify({"ok": True, "started": True}), 200


@on_demand_bp.route("/<camera_id>/stop", methods=["POST"])
def on_demand_stop(camera_id: str):
    """Viewer left — stop the camera stream unless the scheduler still wants it."""
    camera = current_app.store.get_camera(camera_id)
    if camera is None:
        return jsonify({"error": "Camera not found"}), 404

    scheduler = getattr(current_app, "recording_scheduler", None)
    if scheduler is not None and scheduler.needs_stream(camera_id):
        log.debug("on-demand stop %s: scheduler still needs stream", camera_id)
        return (
            jsonify({"ok": True, "kept_running": True, "reason": "scheduler"}),
            200,
        )

    control = getattr(current_app, "camera_control_client", None)
    if control is None:
        return jsonify({"error": "Control client unavailable"}), 503

    if not camera.ip:
        # Nothing to stop at camera level — just clear intent.
        camera.desired_stream_state = "stopped"
        current_app.store.save_camera(camera)
        return jsonify({"ok": True, "stopped": True}), 200

    _, err = control.stop_stream(camera.ip, camera_id=camera.id)
    if err:
        log.warning("on-demand stop %s failed: %s", camera_id, err)
        return jsonify({"error": err}), 502

    camera.desired_stream_state = "stopped"
    current_app.store.save_camera(camera)
    return jsonify({"ok": True, "stopped": True}), 200


class OnDemandCoordinator:
    """Pure-Python coordinator used by the RecordingScheduler.

    This mirrors the blueprint's `stop` logic without going through Flask, so
    the scheduler can call it directly when a schedule window closes.
    """

    def __init__(self, store, control_client, scheduler_ref):
        self._store = store
        self._control = control_client
        self._scheduler_ref = scheduler_ref  # callable returning scheduler

    def stop(self, camera_id: str) -> tuple[bool, str]:
        """Stop the camera stream if no one else needs it.

        Returns (acted, reason). `acted` True iff we issued a stop.
        """
        scheduler = None
        if self._scheduler_ref is not None:
            try:
                scheduler = self._scheduler_ref()
            except Exception:
                scheduler = None
        if scheduler is not None and scheduler.needs_stream(camera_id):
            return False, "scheduler"

        camera = self._store.get_camera(camera_id)
        if camera is None:
            return False, "not_found"
        if camera.desired_stream_state != "running":
            return False, "already_stopped"
        if not camera.ip or self._control is None:
            camera.desired_stream_state = "stopped"
            self._store.save_camera(camera)
            return True, "no_ip"

        _, err = self._control.stop_stream(camera.ip, camera_id=camera.id)
        if err:
            return False, err
        camera.desired_stream_state = "stopped"
        self._store.save_camera(camera)
        return True, "ok"
