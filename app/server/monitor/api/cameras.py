# REQ: SWR-003, SWR-004, SWR-011, SWR-026; RISK: RISK-002, RISK-005, RISK-007, RISK-015; SEC: SC-002; TEST: TC-008, TC-012, TC-030
"""
Camera management API.

Endpoints:
  GET    /cameras              - list all cameras (confirmed + pending)
  POST   /cameras              - register a new camera as pending (admin)
  POST   /cameras/<id>/confirm - confirm a discovered camera (admin)
  PUT    /cameras/<id>         - update name, location, recording mode (admin)
  DELETE /cameras/<id>         - remove camera and revoke cert (admin)
  GET    /cameras/<id>/status  - live status (online, fps, uptime)
  POST   /cameras/scan         - trigger mDNS scan + return camera list (admin)
  POST   /cameras/config-notify - accept config push from camera (HMAC auth)
  POST   /cameras/heartbeat   - periodic liveness + health update from camera (HMAC auth)

Routes are thin — all orchestration is in CameraService.
"""

import hashlib
import hmac
import threading
import time

from flask import Blueprint, current_app, jsonify, request, session

from monitor.auth import admin_required, csrf_protect, login_required

# ── HMAC auth for camera M2M requests ────────────────────────────────────────
# 30-second window is tight enough to prevent meaningful replay while still
# tolerating real-world clock skew between camera and server (ADR-0016).
_HMAC_MAX_AGE = 30  # seconds (was 300 — reduced to shrink replay window)

# Thread-safe lock for per-app nonce cache mutation.
_seen_nonces_lock = threading.Lock()


def _get_seen_nonces() -> dict:
    """Return the per-app replay cache, creating it if needed.

    Stored on the app object (not module-level) so each Flask test app
    gets its own fresh cache — preventing test state bleed.
    """
    app_obj = current_app._get_current_object()
    if not hasattr(app_obj, "_hmac_seen_nonces"):
        app_obj._hmac_seen_nonces = {}
    return app_obj._hmac_seen_nonces


def _record_and_check_replay(camera_id: str, timestamp_str: str, sig: str) -> bool:
    """Return True if this (timestamp, sig) pair has been seen (replay attempt).

    Thread-safe. Automatically expires stale entries.
    """
    key = (timestamp_str, sig)
    now = time.time()

    with _seen_nonces_lock:
        nonces = _get_seen_nonces()
        camera_cache = nonces.setdefault(camera_id, {})
        # Purge expired entries (TTL = _HMAC_MAX_AGE)
        expired = [k for k, exp in camera_cache.items() if exp <= now]
        for k in expired:
            del camera_cache[k]

        if key in camera_cache:
            return True  # replay detected — reject
        camera_cache[key] = now + _HMAC_MAX_AGE
    return False


cameras_bp = Blueprint("cameras", __name__)


def _nudge_scheduler(camera_id: str) -> None:
    """Best-effort: wake the RecordingScheduler for ``camera_id`` now.

    Motion events are typically 3-10 s long. The scheduler's periodic
    tick is 60 s — far too coarse to catch a motion window via polling
    alone. The motion-event POST handler calls this on both start and
    end so the recorder spawns and tears down promptly without waiting
    for the next tick. Never raises; a scheduler that's absent or in a
    bad state just falls through to the periodic tick, which is still
    correct for continuous / schedule modes.
    """
    scheduler = getattr(current_app, "recording_scheduler", None)
    if scheduler is None:
        return
    try:
        scheduler.nudge(camera_id)
    except Exception:
        pass


def _verify_camera_hmac(request) -> tuple[str, str | None]:
    """Verify HMAC-signed camera request. Shared by heartbeat + config-notify.

    Returns (camera_id, error_message). error_message is None on success.
    """
    camera_id = request.headers.get("X-Camera-ID", "")
    timestamp_str = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")

    if not camera_id or not timestamp_str or not signature:
        return "", "Missing auth headers"

    try:
        ts = int(timestamp_str)
    except ValueError:
        return "", "Invalid timestamp"

    now = int(time.time())
    if abs(now - ts) > _HMAC_MAX_AGE:
        return "", "Timestamp expired"

    camera = current_app.store.get_camera(camera_id)
    if not camera or not camera.pairing_secret:
        return "", "Unknown camera"

    body = request.get_data()
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{camera_id}:{timestamp_str}:{body_hash}"
    expected = hmac.new(
        bytes.fromhex(camera.pairing_secret),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return "", "Invalid signature"

    # Replay detection: reject the exact same signed request twice
    if _record_and_check_replay(camera_id, timestamp_str, signature):
        return "", "Duplicate request (replay detected)"

    return camera_id, None


@cameras_bp.route("", methods=["GET"])
@login_required
def list_cameras():
    """List all cameras (confirmed + pending).

    Admins see all fields including internal health metrics and camera IP.
    Viewers see only the fields needed to display and use the camera UI.
    This prevents viewers from mapping network topology or tracking occupancy.
    """
    admin_view = session.get("role") == "admin"
    cameras = current_app.camera_service.list_cameras(admin_view=admin_view)
    return jsonify(cameras), 200


@cameras_bp.route("", methods=["POST"])
@admin_required
@csrf_protect
def add_camera():
    """Register a new camera as pending. Admin only."""
    data = request.get_json(silent=True) or {}
    result, error, status = current_app.camera_service.add_camera(
        camera_id=data.get("id", ""),
        name=data.get("name", ""),
        location=data.get("location", ""),
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@cameras_bp.route("/config-notify", methods=["POST"])
def config_notify():
    """Accept config notification from a camera.

    Auth: HMAC-SHA256 signature using pairing_secret.
    No session/CSRF — this is machine-to-machine (ADR-0015).
    """
    camera_id, err = _verify_camera_hmac(request)
    if err:
        status_code = 401 if err != "Invalid timestamp" else 400
        return jsonify({"error": err}), status_code

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    error, status = current_app.camera_service.accept_camera_config(camera_id, data)
    if error:
        return jsonify({"error": error}), status
    return jsonify({"message": "Config accepted"}), 200


def _report_unknown_heartbeat(request) -> None:
    """Surface a heartbeat from an unknown camera as a pending discovery.

    When the server has deleted a camera but the camera still has stale
    certs and is sending heartbeats, the HMAC check fails with "Unknown
    camera" (no pairing_secret on record). Without this hook, the server
    dashboard stays empty until the camera reboots and hits /pair/register.
    Routing the camera_id header through discovery.report_camera() closes
    that gap — the operator sees "Discovered — waiting to pair" as soon as
    the first post-unpair heartbeat arrives.

    Best-effort: invalid camera_id format or missing discovery service is
    silently ignored. Never raises — this is a side-channel, not the
    heartbeat's primary response path.
    """
    try:
        import re

        camera_id = request.headers.get("X-Camera-ID", "")
        if not camera_id or not re.match(r"^cam-[a-z0-9]{1,48}$", camera_id):
            return
        discovery = getattr(current_app, "discovery_service", None)
        if discovery is None:
            return
        # paired=None: camera still *thinks* it's paired (it's sending a
        # heartbeat). We create a pending row so the admin can re-pair, but
        # we don't flip any existing pending back to online — the next mDNS
        # advert (which reflects real paired state) will settle the value.
        discovery.report_camera(
            camera_id=camera_id,
            ip=request.remote_addr or "",
            firmware_version="",
            paired=None,
        )
    except Exception:
        # Discovery side-effects must not break the 401 response path.
        pass


@cameras_bp.route("/goodbye", methods=["POST"])
def camera_goodbye():
    """Accept a camera-initiated unpair ("forget this server") request.

    Mirrors the dashboard's admin unpair flow but triggered from the camera's
    own /pair page. Authenticated with an HMAC signature over the current
    pairing_secret — which is about to be destroyed, so this is effectively
    the camera's "last words" to the server. After this call:
        * server revokes the cert, drops pairing_secret, sets status=pending
        * server stops any streaming pipeline for this camera
        * camera side separately wipes its local certs and restarts into
          the PAIRING lifecycle state
    No session/CSRF — machine-to-machine like heartbeat/config-notify.
    """
    camera_id, err = _verify_camera_hmac(request)
    if err:
        status_code = 401 if err != "Invalid timestamp" else 400
        return jsonify({"error": err}), status_code

    # Delegate to the same service the admin DELETE route uses so the two
    # unpair paths are guaranteed identical on the server side.
    error, status = current_app.pairing_service.unpair(
        camera_id,
        user="camera",
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status

    # Stop streaming pipelines so the dashboard stops showing live HLS for
    # a camera that is about to forget us. Best-effort: never fail the goodbye
    # just because the pipeline was already torn down.
    try:
        current_app.streaming.stop_camera(camera_id)
    except Exception:
        pass

    return jsonify({"message": "Camera unpaired"}), 200


@cameras_bp.route("/heartbeat", methods=["POST"])
def camera_heartbeat():
    """Accept periodic heartbeat from a camera.

    Updates last_seen, streaming status, and health metrics.
    Returns pending stream config if the server has unsent changes.

    Auth: HMAC-SHA256 signature using pairing_secret (ADR-0016).
    No session/CSRF — this is machine-to-machine.

    Unknown-camera heartbeats also feed discovery: when an admin has deleted
    the camera from the dashboard, the camera keeps sending heartbeats until
    it detects the repeated 401 and resets. Meanwhile we record the (signed)
    request as a new pending camera so the operator sees it immediately
    under "Discovered — waiting to pair" instead of waiting for the camera
    to reboot and hit /pair/register.
    """
    camera_id, err = _verify_camera_hmac(request)
    if err:
        status_code = 401 if err != "Invalid timestamp" else 400
        if err == "Unknown camera":
            _report_unknown_heartbeat(request)
        return jsonify({"error": err}), status_code

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    response, error, status = current_app.camera_service.accept_heartbeat(
        camera_id, data
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(response), 200


# Per-camera rate limit for motion-event POSTs. A stuck detector firing
# faster than this is dropped at the edge so it can't saturate the event
# store. 20 s matches the min_end_frames + cooldown window on the camera
# side — legitimate events can't arrive faster than that anyway.
_MOTION_RATE_LIMIT_SECONDS = 20
_motion_rate_last_start: dict[str, float] = {}
_motion_rate_lock = threading.Lock()


@cameras_bp.route("/motion-event", methods=["POST"])
def camera_motion_event():
    """Accept a motion-detection event from a camera.

    Auth: HMAC-SHA256 signature using pairing_secret (same scheme as
    heartbeat + config-notify). No session/CSRF — machine-to-machine.

    Body JSON:
        {
          "phase": "start" | "end",
          "event_id": "mot-...",     # client-chosen; stable across start+end
          "started_at": "ISO8601Z",  # camera-reported, informational
          "peak_score": float,       # 0.0-1.0
          "peak_pixels_changed": int,
          "duration_seconds": float  # 0 for start, actual duration for end
        }

    On `phase=start` the server creates a new MotionEvent with the
    server-side authoritative timestamp. On `phase=end` it upserts the
    existing event with final duration + peak. An `AUDIT` event is
    emitted for each transition.

    Per-camera rate limit: at most one phase=start per 20 seconds. Excess
    returns 429 and is NOT persisted.
    """
    camera_id, err = _verify_camera_hmac(request)
    if err:
        status_code = 401 if err != "Invalid timestamp" else 400
        return jsonify({"error": err}), status_code

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    phase = data.get("phase")
    if phase not in ("start", "end"):
        return jsonify({"error": "phase must be 'start' or 'end'"}), 400

    event_id = data.get("event_id") or ""
    if not event_id or not isinstance(event_id, str):
        return jsonify({"error": "event_id required"}), 400

    # Rate limit only applies to phase=start — end events are bounded by
    # the preceding start and should never be dropped.
    if phase == "start":
        now = time.time()
        with _motion_rate_lock:
            last = _motion_rate_last_start.get(camera_id, 0.0)
            if now - last < _MOTION_RATE_LIMIT_SECONDS:
                return jsonify({"error": "Rate limited"}), 429
            _motion_rate_last_start[camera_id] = now

    from monitor.models import MotionEvent

    store = current_app.motion_event_store
    server_ts = request.headers.get("X-Timestamp", "")
    # Convert the HMAC-validated X-Timestamp (epoch seconds) to ISO8601 UTC
    # so the event time is always server-authoritative — camera clock
    # skew cannot corrupt clip lookups later.
    try:
        from datetime import UTC, datetime

        iso_server = datetime.fromtimestamp(int(server_ts), UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (ValueError, OSError):
        return jsonify({"error": "Invalid timestamp"}), 400

    if phase == "start":
        evt = MotionEvent(
            id=event_id,
            camera_id=camera_id,
            started_at=iso_server,
            ended_at=None,
            peak_score=float(data.get("peak_score") or 0.0),
            peak_pixels_changed=int(data.get("peak_pixels_changed") or 0),
            duration_seconds=0.0,
        )
        store.append(evt)
        # Event-driven scheduler nudge. The periodic tick is 60 s which
        # is far too slow for motion events (typical event length 3-10 s);
        # without this, the recorder never spawns during motion-mode
        # recording. See ADR-0021 + recording_scheduler.nudge().
        _nudge_scheduler(camera_id)
        current_app.audit.log_event(
            event="MOTION_DETECTED",
            user="camera",
            ip=request.remote_addr or "",
            detail=f"{camera_id} event={event_id} score={evt.peak_score:.3f}",
        )
        return jsonify({"message": "Event recorded", "event_id": event_id}), 200

    # phase == "end"
    existing = store.get(event_id)
    if existing is None:
        # End without a preceding start — create a closed event anyway
        # so callers can't lose information due to a race.
        evt = MotionEvent(
            id=event_id,
            camera_id=camera_id,
            started_at=iso_server,
            ended_at=iso_server,
            peak_score=float(data.get("peak_score") or 0.0),
            peak_pixels_changed=int(data.get("peak_pixels_changed") or 0),
            duration_seconds=float(data.get("duration_seconds") or 0.0),
        )
        store.append(evt)
    else:
        existing.ended_at = iso_server
        existing.peak_score = max(
            existing.peak_score, float(data.get("peak_score") or 0.0)
        )
        existing.peak_pixels_changed = max(
            existing.peak_pixels_changed,
            int(data.get("peak_pixels_changed") or 0),
        )
        existing.duration_seconds = float(data.get("duration_seconds") or 0.0)
        store.append(existing)

    # Nudge the scheduler again on "end" so motion-mode recordings
    # cleanly enter post-roll and stop the recorder when the window
    # closes, without waiting for the 60 s tick.
    _nudge_scheduler(camera_id)

    current_app.audit.log_event(
        event="MOTION_ENDED",
        user="camera",
        ip=request.remote_addr or "",
        detail=(
            f"{camera_id} event={event_id} "
            f"duration={data.get('duration_seconds', 0.0):.1f}s"
        ),
    )

    # Auto-attach clip_ref when the event ends so the dashboard can
    # know immediately whether a saved clip covers this motion. The
    # correlator filters on finalised .mp4 (ignoring .mp4.part), so it
    # returns None if the segment is still being written — in that
    # case the dashboard's click handler will fall back to Live.
    try:
        correlator = getattr(current_app, "motion_clip_correlator", None)
        if correlator is not None:
            current = store.get(event_id)
            if current is not None and not current.clip_ref:
                clip_ref = correlator.find_clip(camera_id, current.started_at)
                if clip_ref is not None:
                    store.attach_clip(event_id, clip_ref)
    except Exception:
        # Correlator is a side-effect; never fail the response on its account.
        pass
    return jsonify({"message": "Event closed", "event_id": event_id}), 200


@cameras_bp.route("/scan", methods=["POST"])
@admin_required
@csrf_protect
def scan_cameras():
    """Trigger an mDNS scan and return current camera list.

    Sends an immediate PTR query for _rtsp._tcp on the local network.
    The background ServiceBrowser processes responses and calls report_camera()
    for any new cameras, which adds them as pending entries.

    Returns the current camera list (same as GET /cameras) so the dashboard
    can update in a single round-trip.
    """
    current_app.discovery_service.trigger_scan()
    cameras = current_app.camera_service.list_cameras(admin_view=True)
    return jsonify(cameras), 200


@cameras_bp.route("/<camera_id>/confirm", methods=["POST"])
@admin_required
@csrf_protect
def confirm_camera(camera_id):
    """Confirm a discovered (pending) camera. Admin only."""
    data = request.get_json(silent=True) or {}
    result, error, status = current_app.camera_service.confirm(
        camera_id,
        name=data.get("name", ""),
        location=data.get("location", ""),
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify(result), status


@cameras_bp.route("/<camera_id>", methods=["PUT"])
@admin_required
@csrf_protect
def update_camera(camera_id):
    """Update camera settings. Admin only."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    error, status = current_app.camera_service.update(
        camera_id,
        data,
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"message": "Camera updated"}), 200


@cameras_bp.route("/<camera_id>", methods=["DELETE"])
@admin_required
@csrf_protect
def delete_camera(camera_id):
    """Remove a camera and revoke its cert. Admin only."""
    # Revoke cert first (if paired)
    if hasattr(current_app, "pairing_service"):
        current_app.pairing_service.unpair(
            camera_id,
            user=session.get("username", ""),
            ip=request.remote_addr or "",
        )

    error, status = current_app.camera_service.delete(
        camera_id,
        user=session.get("username", ""),
        ip=request.remote_addr or "",
    )
    if error:
        return jsonify({"error": error}), status
    return jsonify({"message": "Camera removed"}), 200


@cameras_bp.route("/<camera_id>/status", methods=["GET"])
@login_required
def camera_status(camera_id):
    """Get live status for a camera."""
    result, error = current_app.camera_service.get_camera_status(camera_id)
    if error:
        return jsonify({"error": error}), 404
    return jsonify(result), 200
