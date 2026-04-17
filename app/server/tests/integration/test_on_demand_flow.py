"""End-to-end on-demand streaming flow (ADR-0017).

Simulates: MediaMTX → coordinator → camera start → viewer leaves →
coordinator stop → camera stop. Uses a fake CameraControlClient that
records calls instead of actually reaching out over mTLS.
"""

from unittest.mock import MagicMock

import pytest

from monitor.models import Camera


class FakeControlClient:
    """Records every call so we can assert on them."""

    def __init__(self):
        self.calls: list = []

    def start_stream(self, ip):
        self.calls.append(("start", ip))
        return {"state": "running"}, ""

    def stop_stream(self, ip):
        self.calls.append(("stop", ip))
        return {"state": "stopped"}, ""


@pytest.fixture
def staged(app):
    cam = Camera(
        id="cam-a",
        name="Front",
        status="online",
        ip="10.0.0.7",
        recording_mode="off",
        desired_stream_state="stopped",
    )
    app.store.save_camera(cam)

    fake = FakeControlClient()
    app.camera_control_client = fake
    # Plug a no-op scheduler by default.
    app.recording_scheduler = MagicMock()
    app.recording_scheduler.needs_stream.return_value = False
    return app, fake


class TestOnDemandFlow:
    def test_viewer_open_then_close(self, staged):
        app, fake = staged
        client = app.test_client()

        # Viewer arrives → MediaMTX calls start.
        r = client.post("/internal/on-demand/cam-a/start")
        assert r.status_code == 200
        assert fake.calls == [("start", "10.0.0.7")]
        assert app.store.get_camera("cam-a").desired_stream_state == "running"

        # Viewer leaves → MediaMTX calls stop.
        r = client.post("/internal/on-demand/cam-a/stop")
        assert r.status_code == 200
        assert ("stop", "10.0.0.7") in fake.calls
        assert app.store.get_camera("cam-a").desired_stream_state == "stopped"

    def test_stop_respects_scheduler_need(self, staged):
        app, fake = staged
        client = app.test_client()

        # Get the camera to running state first.
        client.post("/internal/on-demand/cam-a/start")
        fake.calls.clear()

        # Now scheduler insists on keeping it alive.
        app.recording_scheduler.needs_stream.return_value = True
        r = client.post("/internal/on-demand/cam-a/stop")
        assert r.status_code == 200
        assert r.get_json().get("kept_running") is True
        assert fake.calls == []  # no stop sent
        # desired state remains running
        assert app.store.get_camera("cam-a").desired_stream_state == "running"

    def test_start_is_idempotent(self, staged):
        app, fake = staged
        client = app.test_client()

        client.post("/internal/on-demand/cam-a/start")
        fake.calls.clear()

        # Second start call should be a no-op.
        r = client.post("/internal/on-demand/cam-a/start")
        assert r.status_code == 200
        assert fake.calls == []
        assert r.get_json().get("already_running") is True
