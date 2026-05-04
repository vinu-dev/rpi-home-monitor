# REQ: SWR-024, SWR-029; RISK: RISK-012, RISK-014; SEC: SC-012, SC-014, SC-020; TEST: TC-023, TC-026
"""Integration tests for the timestamp backfill API."""

from __future__ import annotations

import time

from monitor.models import Camera
from monitor.services.clip_stamper import StampResult, stamp_sentinel_path


def _write_flat_clip(app, camera_id: str, stem: str) -> None:
    from pathlib import Path

    cam_dir = Path(app.config["RECORDINGS_DIR"]) / camera_id
    cam_dir.mkdir(parents=True, exist_ok=True)
    (cam_dir / f"{stem}.mp4").write_bytes(b"clip")


def test_status_requires_admin(logged_in_client):
    client = logged_in_client("viewer")
    resp = client.get("/api/v1/recordings/timestamp-backfill/status")
    assert resp.status_code == 403


def test_backfill_status_and_start_flow(app, logged_in_client):
    _write_flat_clip(app, "cam-001", "20260420_140000")
    app.store.save_camera(Camera(id="cam-001", name="Front Door", status="online"))

    def _fake_stamp(clip_path, _camera, _server_meta):
        stamp_sentinel_path(clip_path).write_text("ok\n", encoding="utf-8")
        return StampResult(ok=True, reason="stamped", elapsed_ms=1, stamped=True)

    app.clip_stamper.stamp = _fake_stamp
    app.clip_stamper.tools_available = lambda: True

    client = logged_in_client()
    status_before = client.get("/api/v1/recordings/timestamp-backfill/status")
    assert status_before.status_code == 200
    before = status_before.get_json()
    assert before["summary"]["unstamped"] == 1

    start = client.post("/api/v1/recordings/timestamp-backfill")
    assert start.status_code == 202

    deadline = time.time() + 2
    last = None
    while time.time() < deadline:
        resp = client.get("/api/v1/recordings/timestamp-backfill/status")
        last = resp.get_json()
        if last["state"] == "idle" and last["summary"]["unstamped"] == 0:
            break
        time.sleep(0.05)

    assert last is not None
    assert last["summary"]["stamped"] == 1
    assert last["summary"]["unstamped"] == 0
