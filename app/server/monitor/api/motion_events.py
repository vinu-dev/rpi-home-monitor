# REQ: SWR-008, SWR-040; RISK: RISK-005, RISK-016; SEC: SC-015; TEST: TC-019, TC-038
"""
Motion events API + click-through router.

Two responsibilities:

1. ``GET /api/v1/motion-events`` — JSON list for the Events page /
   dashboard badge. Session-authenticated (viewer or better).

2. ``GET /events/<event_id>`` — HTML redirect router that applies the
   "clip on disk -> Recordings, otherwise Live" rule from
   docs/exec-plans/motion-detection.md. No session auth — an authenticated
   session is already enforced by the surrounding pages; this route
   only issues a 302 and never reveals event content by itself.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from flask import Blueprint, current_app, jsonify, redirect, request

from monitor.auth import login_required

log = logging.getLogger("monitor.api.motion_events")

motion_events_bp = Blueprint("motion_events", __name__)
events_router_bp = Blueprint("events_router", __name__)


@motion_events_bp.route("", methods=["GET"])
@login_required
def list_events():
    """List motion events, newest first.

    Query params:
        cam  — optional camera_id filter
        limit — max records (default 100, clamped to [1, 500])

    Side effect: any event missing a clip_ref is correlated against the
    recordings directory on-the-fly. This backfills events that were
    stored before the phase=end auto-attach hook landed, and also covers
    events whose clip was still being written (`.mp4.part`) at end time
    but has since been finalised.
    """
    store = current_app.motion_event_store
    cam = request.args.get("cam", "").strip()
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 500))

    events = store.list_events(camera_id=cam, limit=limit)

    correlator = getattr(current_app, "motion_clip_correlator", None)
    if correlator is not None:
        for evt in events:
            if evt.clip_ref:
                continue
            try:
                ref = correlator.find_clip(evt.camera_id, evt.started_at)
            except Exception:
                ref = None
            if ref is not None:
                store.attach_clip(evt.id, ref)
                evt.clip_ref = ref

    return jsonify([asdict(e) for e in events]), 200


@events_router_bp.route("/events/<event_id>", methods=["GET"])
@login_required
def open_event(event_id):
    """Resolve an event ID to the best available destination.

    - Saved clip covering the event timestamp → 302 to /recordings
    - Otherwise → 302 to /live

    No "event not found" page for pruned events: if the ID isn't in the
    store we still redirect to /live for the camera-id guess extracted
    from the ID (format ``mot-<ts>-<camera_id>``), falling back to the
    dashboard if we can't parse.
    """
    store = current_app.motion_event_store
    correlator = getattr(current_app, "motion_clip_correlator", None)

    event = store.get(event_id)
    if event is None:
        # Best-effort cam_id fallback from the ID pattern. If it doesn't
        # parse, send the user to the dashboard — no dead links.
        cam_id = _camera_from_event_id(event_id)
        if cam_id:
            return redirect(f"/live?cam={cam_id}", code=302)
        return redirect("/dashboard", code=302)

    # Try to attach a clip_ref if one isn't already present.
    if not event.clip_ref and correlator is not None:
        clip_ref = correlator.find_clip(event.camera_id, event.started_at)
        if clip_ref is not None:
            store.attach_clip(event.id, clip_ref)
            event.clip_ref = clip_ref

    if event.clip_ref:
        ref = event.clip_ref
        url = (
            "/recordings?cam="
            + ref["camera_id"]
            + "&date="
            + ref["date"]
            + "&file="
            + ref["filename"]
            + "&seek="
            + str(ref["offset_seconds"])
        )
        return redirect(url, code=302)

    return redirect(f"/live?cam={event.camera_id}", code=302)


def _camera_from_event_id(event_id: str) -> str:
    """Parse `mot-<iso8601compact>-<camera_id>` → camera_id, or ""."""
    if not event_id or not event_id.startswith("mot-"):
        return ""
    # The ID shape is `mot-<timestamp>-<camera_id>` where camera_id itself
    # starts with `cam-`. Find that anchor and return from there.
    idx = event_id.find("-cam-")
    if idx == -1:
        return ""
    return event_id[idx + 1 :]
