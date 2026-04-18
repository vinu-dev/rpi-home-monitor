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


class TestOnDemandEdgeCases:
    """Edge cases and error paths in the on-demand coordinator blueprint."""

    def test_external_request_rejected(self, app):
        """Requests from non-loopback IPs must be blocked."""
        client = app.test_client()
        # environ_base trick to fake a non-loopback remote address
        resp = client.post(
            "/internal/on-demand/cam-a/start",
            environ_base={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert resp.status_code == 403

    def test_start_unknown_camera_returns_404(self, staged):
        app, _ = staged
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-ghost/start")
        assert resp.status_code == 404

    def test_stop_unknown_camera_returns_404(self, staged):
        app, _ = staged
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-ghost/stop")
        assert resp.status_code == 404

    def test_start_no_ip_returns_409(self, app):
        from monitor.models import Camera
        cam = Camera(id="cam-noip", status="online", ip="", desired_stream_state="stopped")
        app.store.save_camera(cam)
        fake = FakeControlClient()
        app.camera_control_client = fake
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-noip/start")
        assert resp.status_code == 409

    def test_start_no_control_client_returns_503(self, app):
        from monitor.models import Camera
        cam = Camera(id="cam-noctrl", status="online", ip="10.0.0.1", desired_stream_state="stopped")
        app.store.save_camera(cam)
        if hasattr(app, "camera_control_client"):
            del app.camera_control_client
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-noctrl/start")
        assert resp.status_code == 503

    def test_start_control_error_returns_502(self, app):
        from unittest.mock import MagicMock
        from monitor.models import Camera
        cam = Camera(id="cam-err", status="online", ip="10.0.0.2", desired_stream_state="stopped")
        app.store.save_camera(cam)
        ctrl = MagicMock()
        ctrl.start_stream.return_value = (None, "camera refused")
        app.camera_control_client = ctrl
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-err/start")
        assert resp.status_code == 502

    def test_stop_no_control_client_returns_503(self, staged):
        app, _ = staged
        from monitor.models import Camera
        cam = Camera(id="cam-nc2", status="online", ip="10.0.0.3", desired_stream_state="running")
        app.store.save_camera(cam)
        if hasattr(app, "camera_control_client"):
            del app.camera_control_client
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-nc2/stop")
        assert resp.status_code == 503

    def test_stop_no_ip_clears_intent(self, app):
        from unittest.mock import MagicMock
        from monitor.models import Camera
        cam = Camera(id="cam-noip2", status="online", ip="", desired_stream_state="running")
        app.store.save_camera(cam)
        ctrl = MagicMock()
        app.camera_control_client = ctrl
        app.recording_scheduler = MagicMock()
        app.recording_scheduler.needs_stream.return_value = False
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-noip2/stop")
        assert resp.status_code == 200
        assert app.store.get_camera("cam-noip2").desired_stream_state == "stopped"
        ctrl.stop_stream.assert_not_called()

    def test_stop_control_error_returns_502(self, staged):
        app, _ = staged
        from unittest.mock import MagicMock
        from monitor.models import Camera
        cam = Camera(id="cam-stopp-err", status="online", ip="10.0.0.9", desired_stream_state="running")
        app.store.save_camera(cam)
        ctrl = MagicMock()
        ctrl.stop_stream.return_value = (None, "camera offline")
        app.camera_control_client = ctrl
        app.recording_scheduler = MagicMock()
        app.recording_scheduler.needs_stream.return_value = False
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-stopp-err/stop")
        assert resp.status_code == 502


class TestOnDemandCoordinatorUnit:
    """Unit tests for OnDemandCoordinator.stop() — all paths."""

    def _make_coordinator(self, app, scheduler_needs=False, has_ip=True, ctrl_err=""):
        from unittest.mock import MagicMock
        from monitor.models import Camera
        from monitor.api.on_demand import OnDemandCoordinator

        cam = Camera(
            id="cam-coord",
            ip="10.0.0.1" if has_ip else "",
            desired_stream_state="running",
        )
        app.store.save_camera(cam)

        ctrl = MagicMock()
        ctrl.stop_stream.return_value = ({}, ctrl_err)

        sched = MagicMock()
        sched.needs_stream.return_value = scheduler_needs

        coord = OnDemandCoordinator(app.store, ctrl, lambda: sched)
        return coord, ctrl, sched

    def test_stop_held_by_scheduler(self, app):
        coord, ctrl, _ = self._make_coordinator(app, scheduler_needs=True)
        acted, reason = coord.stop("cam-coord")
        assert not acted
        assert reason == "scheduler"
        ctrl.stop_stream.assert_not_called()

    def test_stop_camera_not_found(self, app):
        from monitor.api.on_demand import OnDemandCoordinator
        coord = OnDemandCoordinator(app.store, None, None)
        acted, reason = coord.stop("cam-ghost")
        assert not acted
        assert reason == "not_found"

    def test_stop_already_stopped(self, app):
        from unittest.mock import MagicMock
        from monitor.models import Camera
        from monitor.api.on_demand import OnDemandCoordinator
        cam = Camera(id="cam-stopped", ip="10.0.0.1", desired_stream_state="stopped")
        app.store.save_camera(cam)
        coord = OnDemandCoordinator(app.store, MagicMock(), None)
        acted, reason = coord.stop("cam-stopped")
        assert not acted
        assert reason == "already_stopped"

    def test_stop_no_ip_clears_state(self, app):
        coord, ctrl, _ = self._make_coordinator(app, has_ip=False)
        acted, reason = coord.stop("cam-coord")
        assert acted
        assert reason == "no_ip"
        ctrl.stop_stream.assert_not_called()
        assert app.store.get_camera("cam-coord").desired_stream_state == "stopped"

    def test_stop_ctrl_error_not_acted(self, app):
        coord, ctrl, _ = self._make_coordinator(app, ctrl_err="camera offline")
        acted, reason = coord.stop("cam-coord")
        assert not acted
        assert reason == "camera offline"

    def test_stop_success(self, app):
        coord, ctrl, _ = self._make_coordinator(app)
        acted, reason = coord.stop("cam-coord")
        assert acted
        assert reason == "ok"
        assert app.store.get_camera("cam-coord").desired_stream_state == "stopped"
