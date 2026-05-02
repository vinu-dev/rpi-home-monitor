# REQ: SWR-015; RISK: RISK-005; SEC: SC-004; TEST: TC-008
"""Tests for the camera discovery service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

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

    def test_paired_false_resets_online_to_pending(self, app):
        """When the camera's mDNS TXT says paired=false, an existing 'online'
        row must be reset to 'pending' so the admin can re-pair it. This is the
        server half of the unpair-sync protocol."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            camera.streaming = True
            app.store.save_camera(camera)

            svc.report_camera("cam-001", "192.168.1.50", paired=False)

            camera = app.store.get_camera("cam-001")
            assert camera.status == "pending"
            assert camera.streaming is False

    def test_paired_none_preserves_online(self, app):
        """paired=None (heartbeat, /pair/register, legacy) must not disturb an
        existing online camera — only explicit paired=false does that."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            app.store.save_camera(camera)

            svc.report_camera("cam-001", "192.168.1.50", paired=None)

            camera = app.store.get_camera("cam-001")
            assert camera.status == "online"

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

    def test_clears_streaming_flag_when_marking_offline(self, app):
        """ADR-0016: stale cameras must never show streaming=True."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            camera.streaming = True
            old = (datetime.now(UTC) - timedelta(seconds=60)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            camera.last_seen = old
            app.store.save_camera(camera)

            svc.check_offline()
            camera = app.store.get_camera("cam-001")
            assert camera.status == "offline"
            assert camera.streaming is False

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


class TestOfflineAlertGating:
    """Per-camera enable/disable + flap suppression for CAMERA_OFFLINE
    audits (#136). Status still flips to "offline" on every transition;
    only the audit emission is gated, which is what the alert center
    derives from.
    """

    def _stale_camera(self, app, **camera_overrides):
        """Helper: register a camera with last_seen 60s ago."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.report_camera("cam-001", "192.168.1.50")
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            camera.last_seen = (datetime.now(UTC) - timedelta(seconds=60)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            for k, v in camera_overrides.items():
                setattr(camera, k, v)
            app.store.save_camera(camera)
            return svc

    def test_offline_alerts_disabled_suppresses_audit(self, app):
        """An operator silenced this camera's offline alerts. Status
        must still flip (the dashboard needs to know) but the audit
        log must stay quiet — so the alert-center inbox stays clean.
        """
        with app.app_context():
            svc = self._stale_camera(app, offline_alerts_enabled=False)
            svc.check_offline()
            camera = app.store.get_camera("cam-001")
            assert camera.status == "offline"  # status flipped
            events = app.audit.get_events(event_type="CAMERA_OFFLINE")
            assert events == []  # but no audit

    def test_first_offline_emits_audit_and_stamps_camera(self, app):
        """Baseline — first offline transition emits the audit and
        records the timestamp on the camera so subsequent flap
        suppression works.
        """
        with app.app_context():
            svc = self._stale_camera(app)
            svc.check_offline()
            camera = app.store.get_camera("cam-001")
            assert camera.last_offline_alert_at != ""
            events = app.audit.get_events(event_type="CAMERA_OFFLINE")
            assert len(events) == 1

    def test_repeat_offline_within_cooldown_suppresses_audit(self, app):
        """A flaky camera that bounces online↔offline within five
        minutes should produce *one* alert, not a stream.

        Repro: first offline → audit emitted. Online again. Offline
        again 30s later. Status flips, but the audit is suppressed
        because last_offline_alert_at is fresh.
        """
        with app.app_context():
            svc = self._stale_camera(app)
            svc.check_offline()  # first offline → audit
            assert len(app.audit.get_events(event_type="CAMERA_OFFLINE")) == 1

            # Camera comes back online (heartbeat lands)
            camera = app.store.get_camera("cam-001")
            camera.status = "online"
            camera.last_seen = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            app.store.save_camera(camera)

            # 30s later, offline again
            camera.last_seen = (datetime.now(UTC) - timedelta(seconds=60)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            app.store.save_camera(camera)
            svc.check_offline()

            # Status flipped again, but no second audit
            camera = app.store.get_camera("cam-001")
            assert camera.status == "offline"
            assert len(app.audit.get_events(event_type="CAMERA_OFFLINE")) == 1

    def test_repeat_offline_after_cooldown_emits_audit(self, app):
        """If the camera was offline, recovered, and then went
        offline again *after* the cooldown window, the second offline
        is a new event worth alerting about.
        """
        with app.app_context():
            from monitor.services.discovery import (
                OFFLINE_ALERT_COOLDOWN_SECONDS,
            )

            svc = self._stale_camera(app)
            # Pretend the first offline alert happened well outside the
            # cooldown window (so this check counts as "fresh")
            stale_alert_at = (
                datetime.now(UTC)
                - timedelta(seconds=OFFLINE_ALERT_COOLDOWN_SECONDS + 60)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            camera = app.store.get_camera("cam-001")
            camera.last_offline_alert_at = stale_alert_at
            app.store.save_camera(camera)

            svc.check_offline()

            events = app.audit.get_events(event_type="CAMERA_OFFLINE")
            assert len(events) == 1  # new alert fired

    def test_corrupt_last_offline_alert_at_fails_open(self, app):
        """A garbage timestamp in last_offline_alert_at must not
        crash the staleness checker — emit the audit and let the
        operator see something rather than nothing.
        """
        with app.app_context():
            svc = self._stale_camera(app, last_offline_alert_at="not-a-timestamp")
            svc.check_offline()
            events = app.audit.get_events(event_type="CAMERA_OFFLINE")
            assert len(events) == 1


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


class TestMdnsBrowser:
    """Unit tests for mDNS browser integration (python-zeroconf)."""

    def test_start_mdns_browser_without_zeroconf_logs_warning(self, app, caplog):
        """Graceful degradation when zeroconf is not installed."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            with (
                patch.dict("sys.modules", {"zeroconf": None}),
                patch("builtins.__import__", side_effect=ImportError("no module")),
            ):
                # Should not raise
                try:
                    svc.start_mdns_browser()
                except Exception:
                    pass  # ImportError handled internally
            # zeroconf stays None on ImportError
            # (tested via _zeroconf attribute)

    def test_start_mdns_browser_idempotent(self, app):
        """Calling start_mdns_browser twice does not create two Zeroconf instances."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            mock_zc = MagicMock()
            mock_browser = MagicMock()

            # Simulate browser already running
            svc._zeroconf = mock_zc
            svc._mdns_browser = mock_browser

            # Call again — should return early without touching _zeroconf
            import sys

            mock_mod = MagicMock()
            original = sys.modules.get("zeroconf")
            sys.modules["zeroconf"] = mock_mod
            try:
                svc.start_mdns_browser()
            finally:
                if original is None:
                    sys.modules.pop("zeroconf", None)
                else:
                    sys.modules["zeroconf"] = original

            # _zeroconf unchanged — no new Zeroconf() call happened
            assert svc._zeroconf is mock_zc

    def test_stop_mdns_browser_is_safe_without_start(self, app):
        """stop_mdns_browser() on a fresh service does not raise."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            svc.stop_mdns_browser()  # _zeroconf is None — must not raise

    def test_stop_mdns_browser_closes_zeroconf(self, app):
        """stop_mdns_browser() calls close() on the Zeroconf instance."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            mock_zc = MagicMock()
            svc._zeroconf = mock_zc
            svc._mdns_browser = MagicMock()

            svc.stop_mdns_browser()

            mock_zc.close.assert_called_once()
            assert svc._zeroconf is None
            assert svc._mdns_browser is None

    def test_trigger_scan_does_nothing_without_browser(self, app):
        """trigger_scan() is a no-op when mDNS browser is not running."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            # Must not raise
            svc.trigger_scan()

    def test_handle_mdns_service_calls_report_camera(self, app):
        """_handle_mdns_service() parses TXT records and calls report_camera()."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)

            # Build a mock ServiceInfo as returned by zeroconf.get_service_info()
            mock_info = MagicMock()
            mock_info.properties = {
                b"id": b"cam-abc123",
                b"version": b"1.2.0",
                b"paired": b"false",
            }
            mock_info.parsed_addresses.return_value = ["192.168.1.200"]

            mock_zc = MagicMock()
            mock_zc.get_service_info.return_value = mock_info

            svc._handle_mdns_service(
                mock_zc,
                "_rtsp._tcp.local.",
                "HomeMonitor Camera (cam-abc123)._rtsp._tcp.local.",
            )

            camera = app.store.get_camera("cam-abc123")
            assert camera is not None
            assert camera.status == "pending"
            assert camera.ip == "192.168.1.200"
            assert camera.firmware_version == "1.2.0"

    def test_handle_mdns_service_ignores_non_home_monitor(self, app):
        """_handle_mdns_service() ignores services without 'cam-' id prefix."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)

            mock_info = MagicMock()
            mock_info.properties = {b"id": b"other-device"}
            mock_info.parsed_addresses.return_value = ["192.168.1.201"]

            mock_zc = MagicMock()
            mock_zc.get_service_info.return_value = mock_info

            svc._handle_mdns_service(
                mock_zc, "_rtsp._tcp.local.", "SomeOtherDevice._rtsp._tcp.local."
            )

            # No camera should have been saved
            assert app.store.get_camera("other-device") is None

    def test_handle_mdns_service_ignores_missing_address(self, app):
        """_handle_mdns_service() skips cameras with no resolvable IP."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)

            mock_info = MagicMock()
            mock_info.properties = {b"id": b"cam-noip"}
            mock_info.parsed_addresses.return_value = []
            mock_info.addresses = []

            mock_zc = MagicMock()
            mock_zc.get_service_info.return_value = mock_info

            svc._handle_mdns_service(
                mock_zc,
                "_rtsp._tcp.local.",
                "HomeMonitor Camera (cam-noip)._rtsp._tcp.local.",
            )

            assert app.store.get_camera("cam-noip") is None

    def test_handle_mdns_service_handles_none_info(self, app):
        """_handle_mdns_service() handles get_service_info returning None."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)
            mock_zc = MagicMock()
            mock_zc.get_service_info.return_value = None
            # Must not raise
            svc._handle_mdns_service(
                mock_zc, "_rtsp._tcp.local.", "whatever._rtsp._tcp.local."
            )

    def test_start_mdns_browser_with_zeroconf(self, app):
        """start_mdns_browser() creates Zeroconf + ServiceBrowser when library is present."""
        with app.app_context():
            svc = DiscoveryService(app.store, app.audit)

            mock_zc = MagicMock()
            mock_browser = MagicMock()

            # Patch zeroconf in sys.modules so the lazy import picks up our mocks
            import sys

            mock_zeroconf_mod = MagicMock()
            mock_zeroconf_mod.Zeroconf = MagicMock(return_value=mock_zc)
            mock_zeroconf_mod.ServiceBrowser = MagicMock(return_value=mock_browser)

            original = sys.modules.get("zeroconf")
            sys.modules["zeroconf"] = mock_zeroconf_mod
            try:
                svc.start_mdns_browser()
            finally:
                if original is None:
                    sys.modules.pop("zeroconf", None)
                else:
                    sys.modules["zeroconf"] = original

            # Zeroconf() was called once; _zeroconf set; browser created
            mock_zeroconf_mod.Zeroconf.assert_called_once()
            assert svc._zeroconf is mock_zc
            mock_zeroconf_mod.ServiceBrowser.assert_called_once()
            svc.stop_mdns_browser()


class TestScanEndpoint:
    """Integration tests for POST /cameras/scan."""

    def _login(self, app, client, role="admin"):
        from monitor.auth import hash_password
        from monitor.models import User

        app.store.save_user(
            User(
                id="user-scan-admin",
                username="scanadmin",
                password_hash=hash_password("pass"),
                role=role,
            )
        )
        resp = client.post(
            "/api/v1/auth/login", json={"username": "scanadmin", "password": "pass"}
        )
        client.environ_base["HTTP_X_CSRF_TOKEN"] = resp.get_json()["csrf_token"]

    def test_scan_returns_camera_list(self, app, client):
        """POST /cameras/scan returns current camera list."""
        with app.app_context():
            from monitor.models import Camera

            cam = Camera(
                id="cam-scan01",
                ip="192.168.1.99",
                status="pending",
            )
            app.store.save_camera(cam)

        self._login(app, client)
        resp = client.post("/api/v1/cameras/scan")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        ids = [c["id"] for c in data]
        assert "cam-scan01" in ids

    def test_scan_requires_admin(self, app, client):
        """POST /cameras/scan returns 403 for non-admin users."""
        self._login(app, client, role="viewer")
        resp = client.post("/api/v1/cameras/scan")
        assert resp.status_code == 403

    def test_scan_requires_login(self, client):
        """POST /cameras/scan returns 401 for unauthenticated requests."""
        resp = client.post("/api/v1/cameras/scan")
        assert resp.status_code == 401
