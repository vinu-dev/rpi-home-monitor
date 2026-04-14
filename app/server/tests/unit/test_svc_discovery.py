"""Tests for the camera discovery service."""

from datetime import UTC, datetime, timedelta

from monitor.services.discovery import DiscoveryService


class TestReportCamera:
    """Test camera reporting (mDNS/heartbeat)."""

    def test_new_camera_added_as_pending(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            assert camera is not None
            assert camera.status == "pending"
            assert camera.ip == "192.168.1.50"

    def test_new_camera_logs_audit(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            events = app.audit.get_events(event_type="CAMERA_DISCOVERED")
            assert len(events) >= 1

    def test_known_camera_updates_last_seen(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            # Confirm it first
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            app.store.save_camera(camera)
            # Report again
            svc.report_camera("cam-001", "192.168.1.51")
            camera = app.store.get_camera("cam-001")
            assert camera.ip == "192.168.1.51"
            assert camera.status == "online"

    def test_offline_camera_comes_back_online(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            camera.status = "offline"
            app.store.save_camera(camera)
            # Report again
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            assert camera.status == "online"

    def test_pending_stays_pending_on_report(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            assert camera.status == "pending"

    def test_firmware_version_updated(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50", firmware_version="1.0.0")
            camera = app.store.get_camera("cam-001")
            assert camera.firmware_version == "1.0.0"


class TestCheckOffline:
    """Test offline detection."""

    def test_marks_stale_camera_offline(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            # Set last_seen to 60 seconds ago
            old = (datetime.now(UTC) - timedelta(seconds=60)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            camera.last_seen = old
            app.store.save_camera(camera)

            svc.check_offline()
            camera = app.store.get_camera("cam-001")
            assert camera.status == "offline"

    def test_leaves_recent_camera_online(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            app.store.save_camera(camera)

            svc.check_offline()
            camera = app.store.get_camera("cam-001")
            assert camera.status == "online"

    def test_ignores_pending_cameras(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            # pending camera should not be marked offline
            svc.check_offline()
            camera = app.store.get_camera("cam-001")
            assert camera.status == "pending"

    def test_offline_logs_audit(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            old = (datetime.now(UTC) - timedelta(seconds=60)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            camera.last_seen = old
            app.store.save_camera(camera)
            svc.check_offline()
            events = app.audit.get_events(event_type="CAMERA_OFFLINE")
            assert len(events) >= 1


class TestGetCameraStatus:
    """Test camera status retrieval."""

    def test_returns_status(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            status = svc.get_camera_status("cam-001")
            assert status is not None
            assert status["id"] == "cam-001"
            assert status["status"] == "pending"

    def test_returns_none_for_unknown(self, app):
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            assert svc.get_camera_status("cam-nonexistent") is None
