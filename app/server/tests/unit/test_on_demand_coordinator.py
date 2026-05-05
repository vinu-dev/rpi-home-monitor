# REQ: SWR-052; RISK: RISK-001, RISK-017; SEC: SC-016; TEST: TC-001, TC-028
"""Unit tests for the on-demand coordinator blueprint (ADR-0017)."""

from unittest.mock import MagicMock

import pytest

from monitor.models import Camera


@pytest.fixture
def app_with_cam(app):
    """App with one registered camera + mocked control client."""
    cam = Camera(
        id="cam-x",
        name="X",
        status="online",
        ip="192.0.2.50",
        desired_stream_state="stopped",
    )
    app.store.save_camera(cam)
    app.camera_control_client = MagicMock()
    app.camera_control_client.start_stream.return_value = ({"state": "running"}, "")
    app.camera_control_client.stop_stream.return_value = ({"state": "stopped"}, "")
    return app


class TestOnDemandStart:
    def test_start_calls_control_when_stopped(self, app_with_cam):
        app = app_with_cam
        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-x/start")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body.get("started") is True
        app.camera_control_client.start_stream.assert_called_once_with(
            "192.0.2.50",
            camera_id="cam-x",
        )
        cam = app.store.get_camera("cam-x")
        assert cam.desired_stream_state == "running"

    def test_start_noop_when_already_running(self, app_with_cam):
        app = app_with_cam
        cam = app.store.get_camera("cam-x")
        cam.desired_stream_state = "running"
        app.store.save_camera(cam)

        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-x/start")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body.get("already_running") is True
        app.camera_control_client.start_stream.assert_not_called()

    def test_start_404_unknown_camera(self, app_with_cam):
        client = app_with_cam.test_client()
        resp = client.post("/internal/on-demand/cam-nope/start")
        assert resp.status_code == 404


class TestOnDemandStop:
    def test_stop_calls_control_when_no_one_needs_it(self, app_with_cam):
        app = app_with_cam
        cam = app.store.get_camera("cam-x")
        cam.desired_stream_state = "running"
        app.store.save_camera(cam)

        # Scheduler says: nope, don't need it.
        app.recording_scheduler = MagicMock()
        app.recording_scheduler.needs_stream.return_value = False

        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-x/stop")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body.get("stopped") is True
        app.camera_control_client.stop_stream.assert_called_once_with(
            "192.0.2.50",
            camera_id="cam-x",
        )
        cam = app.store.get_camera("cam-x")
        assert cam.desired_stream_state == "stopped"

    def test_stop_kept_running_when_scheduler_needs_it(self, app_with_cam):
        app = app_with_cam
        cam = app.store.get_camera("cam-x")
        cam.desired_stream_state = "running"
        app.store.save_camera(cam)

        app.recording_scheduler = MagicMock()
        app.recording_scheduler.needs_stream.return_value = True

        client = app.test_client()
        resp = client.post("/internal/on-demand/cam-x/stop")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("kept_running") is True
        assert body.get("reason") == "scheduler"
        app.camera_control_client.stop_stream.assert_not_called()


class TestOnDemandAuth:
    def test_non_localhost_rejected_403(self, app_with_cam):
        """Only 127.0.0.1 / ::1 are allowed."""
        client = app_with_cam.test_client()
        # Simulate a non-localhost remote by overriding environ_base.
        client.environ_base["REMOTE_ADDR"] = "192.168.1.50"
        resp = client.post("/internal/on-demand/cam-x/start")
        assert resp.status_code == 403


class TestCoordinatorDirect:
    """The pure-Python coordinator used by the scheduler."""

    def test_stop_keeps_running_when_scheduler_needs(self, tmp_path):
        from monitor.api.on_demand import OnDemandCoordinator
        from monitor.store import Store

        store = Store(str(tmp_path))
        cam = Camera(id="cam-x", ip="10.0.0.5", desired_stream_state="running")
        store.save_camera(cam)

        scheduler = MagicMock()
        scheduler.needs_stream.return_value = True
        control = MagicMock()

        coord = OnDemandCoordinator(store, control, lambda: scheduler)
        acted, reason = coord.stop("cam-x")
        assert acted is False
        assert reason == "scheduler"
        control.stop_stream.assert_not_called()

    def test_stop_acts_when_no_one_needs(self, tmp_path):
        from monitor.api.on_demand import OnDemandCoordinator
        from monitor.store import Store

        store = Store(str(tmp_path))
        cam = Camera(id="cam-x", ip="10.0.0.5", desired_stream_state="running")
        store.save_camera(cam)

        scheduler = MagicMock()
        scheduler.needs_stream.return_value = False
        control = MagicMock()
        control.stop_stream.return_value = ({"state": "stopped"}, "")

        coord = OnDemandCoordinator(store, control, lambda: scheduler)
        acted, reason = coord.stop("cam-x")
        assert acted is True
        assert reason == "ok"
        control.stop_stream.assert_called_once_with("10.0.0.5", camera_id="cam-x")
        assert store.get_camera("cam-x").desired_stream_state == "stopped"
