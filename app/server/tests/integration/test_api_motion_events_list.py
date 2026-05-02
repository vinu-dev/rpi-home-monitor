# REQ: SWR-008; RISK: RISK-005; SEC: SC-015; TEST: TC-019
"""Integration tests for GET /api/v1/motion-events and /events/<id>."""

from __future__ import annotations

from monitor.models import MotionEvent


def _seed(store, count: int = 3, camera_id: str = "cam-001"):
    events = []
    for i in range(count):
        evt = MotionEvent(
            id=f"mot-2026041914{i:02d}00Z-{camera_id}",
            camera_id=camera_id,
            started_at=f"2026-04-19T14:{i:02d}:00Z",
            ended_at=f"2026-04-19T14:{i:02d}:15Z",
            peak_score=0.1 + i * 0.05,
            peak_pixels_changed=1000 + i * 500,
            duration_seconds=15.0,
        )
        store.append(evt)
        events.append(evt)
    return events


class TestListEventsAuth:
    def test_unauthenticated_returns_401(self, app, client):
        resp = client.get("/api/v1/motion-events")
        assert resp.status_code == 401


class TestListEventsResponse:
    def test_empty_store_returns_empty_list(self, app, logged_in_client):
        client = logged_in_client()
        resp = client.get("/api/v1/motion-events")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_returns_newest_first(self, app, logged_in_client):
        _seed(app.motion_event_store, count=3)
        client = logged_in_client()
        resp = client.get("/api/v1/motion-events")
        assert resp.status_code == 200
        body = resp.get_json()
        assert len(body) == 3
        # Newest first — reverse insertion order.
        assert body[0]["started_at"] == "2026-04-19T14:02:00Z"
        assert body[-1]["started_at"] == "2026-04-19T14:00:00Z"

    def test_filter_by_camera(self, app, logged_in_client):
        _seed(app.motion_event_store, count=2, camera_id="cam-001")
        _seed(app.motion_event_store, count=3, camera_id="cam-002")
        client = logged_in_client()

        resp = client.get("/api/v1/motion-events?cam=cam-002")
        body = resp.get_json()
        assert len(body) == 3
        assert all(e["camera_id"] == "cam-002" for e in body)

    def test_limit_clamped_to_500(self, app, logged_in_client):
        client = logged_in_client()
        resp = client.get("/api/v1/motion-events?limit=9999")
        # Empty store, but the request should still return 200 not 400.
        assert resp.status_code == 200

    def test_limit_below_one_is_clamped(self, app, logged_in_client):
        _seed(app.motion_event_store, count=3)
        client = logged_in_client()
        resp = client.get("/api/v1/motion-events?limit=0")
        assert resp.status_code == 200
        # limit=0 clamped to 1 — exactly one event returned.
        assert len(resp.get_json()) == 1

    def test_malformed_limit_falls_back_to_default(self, app, logged_in_client):
        _seed(app.motion_event_store, count=3)
        client = logged_in_client()
        resp = client.get("/api/v1/motion-events?limit=banana")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 3


class TestEventsRouterRedirect:
    def test_unauthenticated_router_denied(self, app, client):
        _seed(app.motion_event_store, count=1)
        resp = client.get("/events/mot-20260419140000Z-cam-001", follow_redirects=False)
        # login_required would redirect to login OR return 401 depending on
        # route type. Accept either as "not authorised".
        assert resp.status_code in (302, 401)

    def test_event_without_clip_redirects_to_live(self, app, logged_in_client):
        _seed(app.motion_event_store, count=1)
        client = logged_in_client()
        resp = client.get("/events/mot-20260419140000Z-cam-001", follow_redirects=False)
        assert resp.status_code == 302
        assert "/live?cam=cam-001" in resp.headers["Location"]

    def test_event_with_explicit_clip_ref_redirects_to_recordings(
        self, app, logged_in_client
    ):
        store = app.motion_event_store
        evt = MotionEvent(
            id="mot-20260419140000Z-cam-001",
            camera_id="cam-001",
            started_at="2026-04-19T14:00:30Z",
            ended_at="2026-04-19T14:00:40Z",
            duration_seconds=10.0,
            clip_ref={
                "camera_id": "cam-001",
                "date": "2026-04-19",
                "filename": "20260419_140000.mp4",
                "offset_seconds": 30,
            },
        )
        store.append(evt)
        client = logged_in_client()

        resp = client.get(f"/events/{evt.id}", follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert "/recordings" in loc
        assert "cam=cam-001" in loc
        assert "date=2026-04-19" in loc
        assert "file=20260419_140000.mp4" in loc
        assert "seek=30" in loc

    def test_event_triggers_correlator_lazy_attach(
        self, app, logged_in_client, tmp_path, monkeypatch
    ):
        """Router calls correlator if clip_ref is absent but a clip exists."""
        # Point RECORDINGS_DIR at a synthesised tree with a matching clip.
        rec_dir = tmp_path / "rec"
        cam = rec_dir / "cam-001"
        cam.mkdir(parents=True)
        (cam / "20260419_140000.mp4").write_bytes(b"fake")

        from monitor.services.motion_clip_correlator import MotionClipCorrelator

        app.motion_clip_correlator = MotionClipCorrelator(
            rec_dir, clip_duration_seconds=180
        )

        evt = MotionEvent(
            id="mot-20260419140030Z-cam-001",
            camera_id="cam-001",
            started_at="2026-04-19T14:00:30Z",
            ended_at="2026-04-19T14:00:40Z",
            duration_seconds=10.0,
            clip_ref=None,
        )
        app.motion_event_store.append(evt)

        client = logged_in_client()
        resp = client.get(f"/events/{evt.id}", follow_redirects=False)

        assert resp.status_code == 302
        assert "/recordings" in resp.headers["Location"]
        # clip_ref should now be persisted.
        reloaded = app.motion_event_store.get(evt.id)
        assert reloaded.clip_ref is not None
        assert reloaded.clip_ref["filename"] == "20260419_140000.mp4"

    def test_unknown_event_redirects_to_live_from_id_hint(self, app, logged_in_client):
        client = logged_in_client()
        resp = client.get("/events/mot-20260419140000Z-cam-001", follow_redirects=False)
        # No such event in the store — best-effort Live redirect using
        # the cam-001 hint parsed out of the ID.
        assert resp.status_code == 302
        assert "/live?cam=cam-001" in resp.headers["Location"]

    def test_unknown_event_with_unparseable_id_redirects_to_dashboard(
        self, app, logged_in_client
    ):
        client = logged_in_client()
        resp = client.get("/events/garbage", follow_redirects=False)
        assert resp.status_code == 302
        assert "/dashboard" in resp.headers["Location"]
