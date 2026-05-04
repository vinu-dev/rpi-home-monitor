# REQ: SWR-029; RISK: RISK-014; SEC: SC-014; TEST: TC-026
"""Tests for the recordings API."""

import os
from pathlib import Path

from monitor.models import Camera
from monitor.services.clip_stamper import stamp_sentinel_path


def _add_camera(app, camera_id="cam-001"):
    """Helper: add a camera to the store."""
    app.store.save_camera(Camera(id=camera_id, name="Test", status="online"))


def _make_clip(app, camera_id, clip_date, time_str, size=1024):
    """Helper: create a fake clip file in the recordings dir."""
    rec_dir = os.path.join(app.config["RECORDINGS_DIR"], camera_id, clip_date)
    os.makedirs(rec_dir, exist_ok=True)
    path = os.path.join(rec_dir, f"{time_str}.mp4")
    with open(path, "wb") as f:
        f.write(b"x" * size)
    return path


class TestListClips:
    """Test GET /api/v1/recordings/<cam-id>."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/recordings/cam-001").status_code == 401

    def test_camera_not_found(self, logged_in_client):
        client = logged_in_client()
        response = client.get("/api/v1/recordings/cam-xxx")
        assert response.status_code == 404

    def test_empty_recordings(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app)
        data = client.get("/api/v1/recordings/cam-001?date=2026-04-09").get_json()
        assert data == []

    def test_lists_clips(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app)
        first = _make_clip(app, "cam-001", "2026-04-09", "14-00-00")
        stamp_sentinel_path(Path(first)).write_text("ok\n", encoding="utf-8")
        _make_clip(app, "cam-001", "2026-04-09", "14-30-00")
        data = client.get("/api/v1/recordings/cam-001?date=2026-04-09").get_json()
        assert len(data) == 2
        assert data[0]["start_time"] == "14:00:00"
        assert data[0]["stamped"] is True
        assert data[1]["stamped"] is False

    def test_viewer_can_list(self, app, logged_in_client):
        client = logged_in_client("viewer")
        _add_camera(app)
        assert (
            client.get("/api/v1/recordings/cam-001?date=2026-04-09").status_code == 200
        )


class TestListDates:
    """Test GET /api/v1/recordings/<cam-id>/dates."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/recordings/cam-001/dates").status_code == 401

    def test_returns_dates(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app)
        _make_clip(app, "cam-001", "2026-04-08", "10-00-00")
        _make_clip(app, "cam-001", "2026-04-09", "14-00-00")
        data = client.get("/api/v1/recordings/cam-001/dates").get_json()
        assert data["dates"] == ["2026-04-08", "2026-04-09"]

    def test_camera_not_found(self, logged_in_client):
        client = logged_in_client()
        assert client.get("/api/v1/recordings/cam-xxx/dates").status_code == 404


class TestLatestClip:
    """Test GET /api/v1/recordings/<cam-id>/latest."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/recordings/cam-001/latest").status_code == 401

    def test_no_recordings(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app)
        assert client.get("/api/v1/recordings/cam-001/latest").status_code == 404

    def test_returns_latest(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app)
        _make_clip(app, "cam-001", "2026-04-09", "14-00-00")
        _make_clip(app, "cam-001", "2026-04-09", "15-30-00")
        data = client.get("/api/v1/recordings/cam-001/latest").get_json()
        assert data["start_time"] == "15:30:00"

    def test_camera_not_found(self, logged_in_client):
        client = logged_in_client()
        assert client.get("/api/v1/recordings/cam-xxx/latest").status_code == 404


class TestDeleteClip:
    """Test DELETE /api/v1/recordings/<cam-id>/<date>/<filename>."""

    def test_requires_admin(self, logged_in_client):
        client = logged_in_client("viewer")
        response = client.delete("/api/v1/recordings/cam-001/2026-04-09/14-00-00.mp4")
        assert response.status_code == 403

    def test_deletes_clip(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app)
        path = _make_clip(app, "cam-001", "2026-04-09", "14-00-00")
        response = client.delete("/api/v1/recordings/cam-001/2026-04-09/14-00-00.mp4")
        assert response.status_code == 200
        assert not os.path.exists(path)

    def test_clip_not_found(self, logged_in_client):
        client = logged_in_client()
        response = client.delete("/api/v1/recordings/cam-001/2026-04-09/nope.mp4")
        assert response.status_code == 404

    def test_invalid_filename(self, logged_in_client):
        client = logged_in_client()
        response = client.delete("/api/v1/recordings/cam-001/2026-04-09/bad.txt")
        assert response.status_code == 400

    def test_delete_logs_audit(self, app, logged_in_client):
        client = logged_in_client()
        _add_camera(app)
        _make_clip(app, "cam-001", "2026-04-09", "14-00-00")
        client.delete("/api/v1/recordings/cam-001/2026-04-09/14-00-00.mp4")
        events = app.audit.get_events(event_type="CLIP_DELETED")
        assert len(events) >= 1
