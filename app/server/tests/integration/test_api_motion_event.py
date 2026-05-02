# REQ: SWR-008; RISK: RISK-005; SEC: SC-015; TEST: TC-019
"""Integration tests for POST /api/v1/cameras/motion-event (HMAC)."""

from __future__ import annotations

import hashlib
import hmac as _hmac_lib
import json
import time as _time

from monitor.models import Camera

_PAIRING_SECRET = "deadbeef" * 8  # 64 hex chars = 32 bytes


def _pair_camera(app, camera_id: str = "cam-001"):
    cam = Camera(
        id=camera_id,
        status="online",
        ip="192.168.1.50",
        pairing_secret=_PAIRING_SECRET,
    )
    app.store.save_camera(cam)
    return cam


def _sign(camera_id: str, body: bytes) -> dict:
    ts = str(int(_time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    msg = f"{camera_id}:{ts}:{body_hash}"
    sig = _hmac_lib.new(
        bytes.fromhex(_PAIRING_SECRET),
        msg.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Camera-ID": camera_id,
        "X-Timestamp": ts,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }


def _reset_motion_rate_limit(app):
    """The rate-limit dict is module-level — reset it between tests."""
    from monitor.api import cameras as cameras_mod

    cameras_mod._motion_rate_last_start.clear()


class TestMotionEventAuth:
    def test_missing_headers_returns_401(self, app, client):
        _pair_camera(app)
        resp = client.post("/api/v1/cameras/motion-event", json={})
        assert resp.status_code == 401

    def test_bad_signature_returns_401(self, app, client):
        _pair_camera(app)
        body = json.dumps({"phase": "start", "event_id": "mot-1"}).encode()
        headers = _sign("cam-001", body)
        headers["X-Signature"] = "0" * 64
        resp = client.post("/api/v1/cameras/motion-event", data=body, headers=headers)
        assert resp.status_code == 401

    def test_unknown_camera_returns_401(self, app, client):
        body = json.dumps({"phase": "start", "event_id": "mot-1"}).encode()
        headers = _sign("cam-unknown", body)
        resp = client.post("/api/v1/cameras/motion-event", data=body, headers=headers)
        assert resp.status_code == 401


class TestMotionEventValidation:
    def test_missing_body_returns_400(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)
        headers = _sign("cam-001", b"{}")
        resp = client.post("/api/v1/cameras/motion-event", data=b"{}", headers=headers)
        # Empty JSON body — handler rejects before phase validation.
        assert resp.status_code == 400
        assert "JSON body required" in resp.get_json()["error"]

    def test_bad_phase_returns_400(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)
        body = json.dumps({"phase": "banana", "event_id": "mot-1"}).encode()
        headers = _sign("cam-001", body)
        resp = client.post("/api/v1/cameras/motion-event", data=body, headers=headers)
        assert resp.status_code == 400

    def test_missing_event_id_returns_400(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)
        body = json.dumps({"phase": "start"}).encode()
        headers = _sign("cam-001", body)
        resp = client.post("/api/v1/cameras/motion-event", data=body, headers=headers)
        assert resp.status_code == 400


class TestMotionEventPersistence:
    def test_start_phase_creates_event(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)
        body = json.dumps(
            {
                "phase": "start",
                "event_id": "mot-abc",
                "peak_score": 0.1,
                "peak_pixels_changed": 1500,
            }
        ).encode()
        headers = _sign("cam-001", body)

        resp = client.post("/api/v1/cameras/motion-event", data=body, headers=headers)
        assert resp.status_code == 200

        stored = app.motion_event_store.get("mot-abc")
        assert stored is not None
        assert stored.camera_id == "cam-001"
        assert stored.ended_at is None
        assert stored.peak_score == 0.1
        # started_at must be server-stamped (ISO8601 Z) not camera time.
        assert stored.started_at.endswith("Z")

    def test_end_phase_upserts_existing_event(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)

        # Start.
        body = json.dumps(
            {"phase": "start", "event_id": "mot-xyz", "peak_score": 0.05}
        ).encode()
        client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )

        # End — rate limit doesn't apply to `end`.
        body = json.dumps(
            {
                "phase": "end",
                "event_id": "mot-xyz",
                "peak_score": 0.22,
                "duration_seconds": 14.0,
            }
        ).encode()
        resp = client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )
        assert resp.status_code == 200

        stored = app.motion_event_store.get("mot-xyz")
        assert stored is not None
        assert stored.ended_at is not None
        assert stored.ended_at.endswith("Z")
        # Peak score should be the MAX of start + end reports.
        assert stored.peak_score == 0.22
        assert stored.duration_seconds == 14.0

    def test_end_without_start_creates_closed_event(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)
        body = json.dumps(
            {
                "phase": "end",
                "event_id": "mot-orphan",
                "peak_score": 0.3,
                "duration_seconds": 8.0,
            }
        ).encode()
        resp = client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )
        assert resp.status_code == 200

        stored = app.motion_event_store.get("mot-orphan")
        assert stored is not None
        assert stored.started_at == stored.ended_at  # same timestamp


class TestMotionEventAudit:
    def test_start_emits_motion_detected_audit(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)
        body = json.dumps(
            {"phase": "start", "event_id": "mot-audit-1", "peak_score": 0.15}
        ).encode()
        client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )

        events = app.audit.get_events(event_type="MOTION_DETECTED")
        assert any("mot-audit-1" in e["detail"] for e in events)

    def test_end_emits_motion_ended_audit(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)
        body = json.dumps(
            {
                "phase": "end",
                "event_id": "mot-audit-2",
                "duration_seconds": 12.0,
            }
        ).encode()
        client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )

        events = app.audit.get_events(event_type="MOTION_ENDED")
        assert any("mot-audit-2" in e["detail"] for e in events)


class TestMotionEventRateLimit:
    def test_second_start_within_window_returns_429(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)

        body = json.dumps(
            {"phase": "start", "event_id": "mot-rl-1", "peak_score": 0.1}
        ).encode()
        first = client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )
        assert first.status_code == 200

        # Second start from the same camera within 20 s.
        body = json.dumps(
            {"phase": "start", "event_id": "mot-rl-2", "peak_score": 0.1}
        ).encode()
        second = client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )
        assert second.status_code == 429

        # The rate-limited event must NOT be persisted.
        assert app.motion_event_store.get("mot-rl-2") is None

    def test_end_phase_is_not_rate_limited(self, app, client):
        _pair_camera(app)
        _reset_motion_rate_limit(app)

        body = json.dumps({"phase": "start", "event_id": "mot-rl-e1"}).encode()
        client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )

        # Even within the rate-limit window, end is allowed.
        body = json.dumps(
            {"phase": "end", "event_id": "mot-rl-e1", "duration_seconds": 5.0}
        ).encode()
        resp = client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )
        assert resp.status_code == 200

    def test_rate_limit_per_camera_independent(self, app, client):
        _pair_camera(app, "cam-001")
        _pair_camera(app, "cam-002")
        _reset_motion_rate_limit(app)

        body = json.dumps({"phase": "start", "event_id": "mot-pc-1"}).encode()
        first = client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-001", body)
        )
        assert first.status_code == 200

        # cam-002 can still fire even though cam-001 is rate-limited.
        body = json.dumps({"phase": "start", "event_id": "mot-pc-2"}).encode()
        second = client.post(
            "/api/v1/cameras/motion-event", data=body, headers=_sign("cam-002", body)
        )
        assert second.status_code == 200
