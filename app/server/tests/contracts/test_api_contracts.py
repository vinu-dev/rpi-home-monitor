"""
API contract tests — verify exact response field names for every endpoint.

These tests catch silent API drift: a renamed field won't break server-side
tests but will break the frontend. Contract tests make field names explicit.

Layer 4 of the testing pyramid (see docs/development-guide.md Section 3.8).
"""

import os
import time
from unittest.mock import MagicMock, patch

from monitor.auth import hash_password
from monitor.models import Camera, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login(app, client, role="admin"):
    """Create a user and log in, return the CSRF token."""
    app.store.save_user(
        User(
            id="user-test",
            username="testadmin",
            password_hash=hash_password("testpass"),
            role=role,
            created_at="2026-01-01T00:00:00Z",
        )
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "testadmin", "password": "testpass"},
    )
    client.environ_base["HTTP_X_CSRF_TOKEN"] = resp.get_json().get("csrf_token", "")
    return resp.get_json().get("csrf_token", "")


def _add_camera(app, cam_id="cam-001", status="online"):
    """Add a camera to the store."""
    cam = Camera(
        id=cam_id,
        name="Test Camera",
        location="Front",
        status=status,
        ip="192.168.1.50",
        recording_mode="continuous",
    )
    app.store.save_camera(cam)
    return cam


def _assert_fields(data, required_fields, msg=""):
    """Assert that data dict contains exactly the required top-level keys."""
    actual = set(data.keys())
    missing = required_fields - actual
    extra = actual - required_fields
    assert not missing, f"Missing fields {missing} in response. {msg}"
    assert not extra, f"Unexpected fields {extra} in response. {msg}"


def _assert_has_fields(data, required_fields, msg=""):
    """Assert that data dict contains at least the required keys."""
    actual = set(data.keys())
    missing = required_fields - actual
    assert not missing, f"Missing fields {missing} in response. {msg}"


# ===========================================================================
# Auth contracts (/api/v1/auth/*)
# ===========================================================================


class TestAuthLoginContract:
    """POST /api/v1/auth/login — response field names."""

    def test_success_fields(self, app, client):
        app.store.save_user(
            User(
                id="user-1",
                username="admin",
                password_hash=hash_password("pass"),
                role="admin",
            )
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "pass"},
        )
        data = resp.get_json()
        _assert_fields(data, {"user", "csrf_token"})
        _assert_fields(data["user"], {"id", "username", "role"}, msg="login.user")

    def test_error_fields(self, app, client):
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": "wrong", "password": "wrong"},
        )
        data = resp.get_json()
        _assert_fields(data, {"error"})


class TestAuthLogoutContract:
    """POST /api/v1/auth/logout — response field names."""

    def test_success_fields(self, app, client):
        resp = client.post("/api/v1/auth/logout")
        data = resp.get_json()
        _assert_fields(data, {"message"})


class TestAuthMeContract:
    """GET /api/v1/auth/me — response field names."""

    def test_success_fields(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/auth/me")
        data = resp.get_json()
        _assert_fields(data, {"user", "csrf_token"})
        _assert_fields(data["user"], {"id", "username", "role"}, msg="me.user")

    def test_unauthenticated_error(self, client):
        resp = client.get("/api/v1/auth/me")
        data = resp.get_json()
        _assert_fields(data, {"error"})


# ===========================================================================
# Camera contracts (/api/v1/cameras/*)
# ===========================================================================

# Admin sees all fields (network + health metrics)
CAMERA_LIST_FIELDS_ADMIN = {
    "id",
    "name",
    "location",
    "status",
    "ip",
    "recording_mode",
    "resolution",
    "fps",
    "paired_at",
    "last_seen",
    "firmware_version",
    "width",
    "height",
    "bitrate",
    "h264_profile",
    "keyframe_interval",
    "rotation",
    "hflip",
    "vflip",
    "config_sync",
    # ADR-0016: live health fields sent via heartbeat
    "streaming",
    "cpu_temp",
    "memory_percent",
    "uptime_seconds",
    # ADR-0017: recording-mode + on-demand streaming fields
    "recording_schedule",
    "recording_motion_enabled",
    "desired_stream_state",
}

# Viewers see a subset — no IP (network topology) or health metrics (occupancy risk)
CAMERA_LIST_FIELDS_VIEWER = CAMERA_LIST_FIELDS_ADMIN - {
    "ip",
    "cpu_temp",
    "memory_percent",
    "uptime_seconds",
}

# Backwards-compat alias (most tests use admin login)
CAMERA_LIST_FIELDS = CAMERA_LIST_FIELDS_ADMIN


class TestCamerasListContract:
    """GET /api/v1/cameras — array of camera objects."""

    def test_camera_object_fields_admin(self, app, client):
        """Admin sees full camera detail including IP and health metrics."""
        _login(app, client)
        _add_camera(app)
        data = client.get("/api/v1/cameras").get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        _assert_fields(data[0], CAMERA_LIST_FIELDS_ADMIN)

    def test_camera_object_fields_viewer(self, app, client):
        """Viewer sees limited fields — no IP or health metrics."""
        _login(app, client, role="viewer")
        _add_camera(app)
        data = client.get("/api/v1/cameras").get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        cam = data[0]
        # Viewer should NOT see admin-only fields
        for field in ("ip", "cpu_temp", "memory_percent", "uptime_seconds"):
            assert field not in cam, f"Admin field '{field}' leaked to viewer"
        # Viewer should see all viewer-accessible fields
        for field in CAMERA_LIST_FIELDS_VIEWER:
            assert field in cam, f"Expected viewer field '{field}' missing"

    def test_excludes_sensitive_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        data = client.get("/api/v1/cameras").get_json()
        cam = data[0]
        for field in ["rtsp_url", "cert_serial", "password", "pairing_secret"]:
            assert field not in cam, f"Sensitive field '{field}' leaked"


class TestCameraAddContract:
    """POST /api/v1/cameras."""

    def test_success_fields(self, app, client):
        _login(app, client)
        resp = client.post(
            "/api/v1/cameras",
            json={"id": "cam-new", "name": "Front", "location": "Yard"},
        )
        data = resp.get_json()
        _assert_has_fields(data, {"id", "name", "status"})
        assert data["status"] == "pending"

    def test_duplicate_error(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.post("/api/v1/cameras", json={"id": "cam-001"})
        data = resp.get_json()
        _assert_fields(data, {"error"})


class TestCameraConfirmContract:
    """POST /api/v1/cameras/<id>/confirm."""

    def test_success_fields(self, app, client):
        _login(app, client)
        _add_camera(app, status="pending")
        resp = client.post(
            "/api/v1/cameras/cam-001/confirm",
            json={"name": "Front Door"},
        )
        data = resp.get_json()
        _assert_has_fields(data, {"id", "name", "status", "paired_at"})

    def test_not_found_error(self, app, client):
        _login(app, client)
        resp = client.post(
            "/api/v1/cameras/nonexistent/confirm",
            json={"name": "X"},
        )
        data = resp.get_json()
        _assert_fields(data, {"error"})


class TestCameraStatusContract:
    """GET /api/v1/cameras/<id>/status."""

    def test_success_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.get("/api/v1/cameras/cam-001/status")
        data = resp.get_json()
        _assert_has_fields(
            data,
            {
                "id",
                "name",
                "status",
                "ip",
                "last_seen",
                "firmware_version",
                "resolution",
                "fps",
                "recording_mode",
            },
        )


# ===========================================================================
# Setup / provisioning contracts (/api/v1/setup/*)
# ===========================================================================


class TestSetupStatusContract:
    """GET /api/v1/setup/status."""

    def test_fields(self, client):
        resp = client.get("/api/v1/setup/status")
        data = resp.get_json()
        _assert_has_fields(data, {"setup_complete"})


class TestSetupWifiScanContract:
    """GET /api/v1/setup/wifi/scan."""

    @patch("monitor.services.provisioning_service.subprocess")
    def test_success_fields(self, mock_sub, client):
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout="TestNet:80:WPA2\n",
            stderr="",
        )
        resp = client.get("/api/v1/setup/wifi/scan")
        data = resp.get_json()
        _assert_fields(data, {"networks"})
        assert isinstance(data["networks"], list)
        if data["networks"]:
            net = data["networks"][0]
            _assert_fields(net, {"ssid", "signal", "security"})


class TestSetupWifiSaveContract:
    """POST /api/v1/setup/wifi/save."""

    def test_success_fields(self, client):
        resp = client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "TestNet", "password": "testpass"},
        )
        data = resp.get_json()
        _assert_fields(data, {"message"})

    def test_error_fields(self, client):
        resp = client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "", "password": ""},
        )
        data = resp.get_json()
        _assert_fields(data, {"error"})


class TestSetupAdminContract:
    """POST /api/v1/setup/admin."""

    def test_error_fields(self, client):
        resp = client.post(
            "/api/v1/setup/admin",
            json={"password": "short"},
        )
        data = resp.get_json()
        _assert_fields(data, {"error"})


class TestSetupCompleteContract:
    """POST /api/v1/setup/complete."""

    @patch("monitor.services.provisioning_service.subprocess.run")
    def test_success_fields(self, mock_run, app, client):
        # Save WiFi first
        client.post(
            "/api/v1/setup/wifi/save",
            json={"ssid": "TestNet", "password": "testpass123"},
        )
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="IP4.ADDRESS[1]:192.168.1.42/24\n",
            stderr="",
        )
        resp = client.post("/api/v1/setup/complete")
        data = resp.get_json()
        _assert_has_fields(data, {"ip", "hostname"})

    def test_error_when_no_wifi(self, app, client):
        # Reset pending WiFi
        app.provisioning_service._pending_wifi["ssid"] = ""
        app.provisioning_service._pending_wifi["password"] = ""
        resp = client.post("/api/v1/setup/complete")
        data = resp.get_json()
        _assert_fields(data, {"error"})


# ===========================================================================
# Users contracts (/api/v1/users/*)
# ===========================================================================

USER_LIST_FIELDS = {"id", "username", "role", "created_at", "last_login"}


class TestUsersListContract:
    """GET /api/v1/users."""

    def test_user_object_fields(self, app, client):
        _login(app, client)
        data = client.get("/api/v1/users").get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        _assert_fields(data[0], USER_LIST_FIELDS)

    def test_excludes_password_hash(self, app, client):
        _login(app, client)
        data = client.get("/api/v1/users").get_json()
        for user in data:
            assert "password_hash" not in user


class TestUsersCreateContract:
    """POST /api/v1/users."""

    def test_success_fields(self, app, client):
        _login(app, client)
        resp = client.post(
            "/api/v1/users",
            json={"username": "newuser", "password": "securepass123", "role": "viewer"},
        )
        data = resp.get_json()
        _assert_has_fields(data, {"id", "username", "role", "created_at"})

    def test_error_fields(self, app, client):
        _login(app, client)
        resp = client.post(
            "/api/v1/users",
            json={"username": "", "password": ""},
        )
        data = resp.get_json()
        _assert_fields(data, {"error"})


class TestUsersDeleteContract:
    """DELETE /api/v1/users/<id>."""

    def test_success_fields(self, app, client):
        _login(app, client)
        # Create a user to delete
        app.store.save_user(
            User(
                id="user-del",
                username="todelete",
                password_hash=hash_password("pass"),
                role="viewer",
            )
        )
        resp = client.delete("/api/v1/users/user-del")
        data = resp.get_json()
        _assert_fields(data, {"message"})


class TestUsersChangePasswordContract:
    """PUT /api/v1/users/<id>/password."""

    def test_success_fields(self, app, client):
        _login(app, client)
        resp = client.put(
            "/api/v1/users/user-test/password",
            json={"new_password": "newsecurepass123"},
        )
        data = resp.get_json()
        _assert_fields(data, {"message"})


# ===========================================================================
# Settings contracts (/api/v1/settings/*)
# ===========================================================================

SETTINGS_FIELDS = {
    "timezone",
    "storage_threshold_percent",
    "clip_duration_seconds",
    "session_timeout_minutes",
    "hostname",
    "setup_completed",
    "firmware_version",
}


class TestSettingsGetContract:
    """GET /api/v1/settings."""

    def test_fields(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/settings")
        data = resp.get_json()
        _assert_has_fields(data, SETTINGS_FIELDS)


class TestSettingsUpdateContract:
    """PUT /api/v1/settings."""

    def test_success_fields(self, app, client):
        _login(app, client)
        resp = client.put(
            "/api/v1/settings",
            json={"timezone": "US/Eastern"},
        )
        data = resp.get_json()
        _assert_fields(data, {"message"})

    def test_error_fields(self, app, client):
        _login(app, client)
        resp = client.put(
            "/api/v1/settings",
            json={"timezone": ""},
        )
        data = resp.get_json()
        _assert_fields(data, {"error"})


# ===========================================================================
# System contracts (/api/v1/system/*)
# ===========================================================================

HEALTH_FIELDS = {
    "cpu_temp_c",
    "cpu_usage_percent",
    "memory",
    "disk",
    "uptime",
    "warnings",
    "status",
}


class TestSystemHealthContract:
    """GET /api/v1/system/health."""

    def test_fields(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/system/health")
        data = resp.get_json()
        _assert_has_fields(data, HEALTH_FIELDS)
        # Nested objects
        _assert_has_fields(
            data["memory"],
            {"total_mb", "used_mb", "free_mb", "percent"},
            msg="health.memory",
        )
        _assert_has_fields(
            data["disk"],
            {"total_gb", "used_gb", "free_gb", "percent"},
            msg="health.disk",
        )
        _assert_has_fields(
            data["uptime"],
            {"seconds", "display"},
            msg="health.uptime",
        )


class TestSystemInfoContract:
    """GET /api/v1/system/info."""

    def test_fields(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/system/info")
        data = resp.get_json()
        _assert_fields(
            data,
            {
                "hostname",
                "firmware_version",
                "uptime",
                "os_name",
                "os_version",
                "os_build",
                "os_variant",
            },
        )


class TestSystemSummaryContract:
    """GET /api/v1/system/summary (ADR-0018 dashboard status strip)."""

    def test_top_level_shape(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/system/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        _assert_fields(data, {"state", "summary", "details", "deep_link"})
        assert data["state"] in {"green", "amber", "red"}
        assert isinstance(data["summary"], str) and data["summary"]
        assert isinstance(data["deep_link"], str) and data["deep_link"].startswith("/")

    def test_details_shape(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/system/summary")
        data = resp.get_json()
        _assert_fields(
            data["details"],
            {"cameras", "storage", "recorder", "recent_errors"},
            msg="summary.details",
        )
        _assert_has_fields(
            data["details"]["cameras"],
            {"online", "total", "offline_names"},
            msg="summary.details.cameras",
        )
        _assert_has_fields(
            data["details"]["storage"],
            {"percent", "retention_days", "free_gb", "total_gb"},
            msg="summary.details.storage",
        )
        _assert_has_fields(
            data["details"]["recorder"],
            {"cpu_percent", "cpu_temp_c", "memory_percent"},
            msg="summary.details.recorder",
        )

    def test_requires_login(self, client):
        resp = client.get("/api/v1/system/summary")
        assert resp.status_code == 401


# ===========================================================================
# Recordings contracts (/api/v1/recordings/*)
# ===========================================================================

CLIP_FIELDS = {
    "camera_id",
    "filename",
    "date",
    "start_time",
    "duration_seconds",
    "size_bytes",
    "thumbnail",
}


class TestRecordingsListContract:
    """GET /api/v1/recordings/<cam-id>."""

    def test_clip_object_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        # Create a fake clip
        rec_dir = app.config["RECORDINGS_DIR"]
        clip_dir = os.path.join(rec_dir, "cam-001", "2026-04-11")
        os.makedirs(clip_dir, exist_ok=True)
        with open(os.path.join(clip_dir, "14-30-00.mp4"), "wb") as f:
            f.write(b"\x00" * 1024)

        resp = client.get("/api/v1/recordings/cam-001?date=2026-04-11")
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        _assert_fields(data[0], CLIP_FIELDS)

    def test_empty_returns_list(self, app, client):
        _login(app, client)
        _add_camera(app)
        data = client.get("/api/v1/recordings/cam-001?date=2026-01-01").get_json()
        assert isinstance(data, list)
        assert len(data) == 0


class TestRecordingsDatesContract:
    """GET /api/v1/recordings/<cam-id>/dates."""

    def test_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.get("/api/v1/recordings/cam-001/dates")
        data = resp.get_json()
        _assert_fields(data, {"camera_id", "dates"})
        assert isinstance(data["dates"], list)


class TestRecordingsLatestContract:
    """GET /api/v1/recordings/<cam-id>/latest."""

    def test_success_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        rec_dir = app.config["RECORDINGS_DIR"]
        clip_dir = os.path.join(rec_dir, "cam-001", "2026-04-11")
        os.makedirs(clip_dir, exist_ok=True)
        with open(os.path.join(clip_dir, "14-30-00.mp4"), "wb") as f:
            f.write(b"\x00" * 1024)

        resp = client.get("/api/v1/recordings/cam-001/latest")
        data = resp.get_json()
        _assert_fields(data, CLIP_FIELDS)

    def test_no_clips_error(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.get("/api/v1/recordings/cam-001/latest")
        data = resp.get_json()
        _assert_fields(data, {"error"})


class TestRecordingsLatestAcrossContract:
    """GET /api/v1/recordings/latest — cross-camera newest clip (ADR-0018)."""

    def test_success_fields(self, app, client):
        _login(app, client)
        _add_camera(app, cam_id="cam-a")
        _add_camera(app, cam_id="cam-b")
        rec_dir = app.config["RECORDINGS_DIR"]
        # Backdate past the in-progress-clip guard
        # (_ACTIVE_WRITE_SECONDS=10 in recordings_service).
        old = time.time() - 60
        for cam, t in (("cam-a", "10-00-00"), ("cam-b", "14-30-00")):
            clip_dir = os.path.join(rec_dir, cam, "2026-04-11")
            os.makedirs(clip_dir, exist_ok=True)
            clip_path = os.path.join(clip_dir, f"{t}.mp4")
            with open(clip_path, "wb") as f:
                f.write(b"\x00" * 1024)
            os.utime(clip_path, (old, old))

        resp = client.get("/api/v1/recordings/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        _assert_has_fields(data, CLIP_FIELDS | {"camera_name"})

    def test_no_clips_error(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/recordings/latest")
        assert resp.status_code == 404
        _assert_fields(resp.get_json(), {"error"})


class TestRecordingsRecentContract:
    """GET /api/v1/recordings/recent?limit=N — recent events feed (ADR-0018)."""

    def test_returns_list_with_camera_name(self, app, client):
        _login(app, client)
        _add_camera(app, cam_id="cam-a")
        rec_dir = app.config["RECORDINGS_DIR"]
        clip_dir = os.path.join(rec_dir, "cam-a", "2026-04-11")
        os.makedirs(clip_dir, exist_ok=True)
        old = time.time() - 60
        for t in ("10-00-00", "14-30-00", "18-00-00"):
            clip_path = os.path.join(clip_dir, f"{t}.mp4")
            with open(clip_path, "wb") as f:
                f.write(b"\x00" * 512)
            os.utime(clip_path, (old, old))

        resp = client.get("/api/v1/recordings/recent?limit=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list) and data
        _assert_has_fields(data[0], CLIP_FIELDS | {"camera_name"})

    def test_empty_returns_empty_list(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/recordings/recent")
        assert resp.status_code == 200
        assert resp.get_json() == []


class TestRecordingsDeleteContract:
    """DELETE /api/v1/recordings/<cam-id>/<date>/<filename>."""

    def test_success_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        rec_dir = app.config["RECORDINGS_DIR"]
        clip_dir = os.path.join(rec_dir, "cam-001", "2026-04-11")
        os.makedirs(clip_dir, exist_ok=True)
        with open(os.path.join(clip_dir, "14-30-00.mp4"), "wb") as f:
            f.write(b"\x00" * 1024)

        resp = client.delete("/api/v1/recordings/cam-001/2026-04-11/14-30-00.mp4")
        data = resp.get_json()
        _assert_fields(data, {"message"})

    def test_not_found_error(self, app, client):
        _login(app, client)
        resp = client.delete("/api/v1/recordings/cam-001/2026-01-01/nope.mp4")
        data = resp.get_json()
        _assert_fields(data, {"error"})


class TestRecordingsCamerasContract:
    """GET /api/v1/recordings/cameras — paired + orphan archive list."""

    def test_paired_camera_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.get("/api/v1/recordings/cameras")
        data = resp.get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        _assert_fields(data[0], {"id", "name", "status"})
        assert data[0]["status"] in {"online", "offline", "removed"}

    def test_orphan_surfaces_as_removed(self, app, client):
        _login(app, client)
        # No Camera record — just a clip on disk.
        rec_dir = app.config["RECORDINGS_DIR"]
        clip_dir = os.path.join(rec_dir, "cam-orphan", "2026-04-11")
        os.makedirs(clip_dir, exist_ok=True)
        with open(os.path.join(clip_dir, "14-30-00.mp4"), "wb") as f:
            f.write(b"\x00" * 64)

        data = client.get("/api/v1/recordings/cameras").get_json()
        entry = next((c for c in data if c["id"] == "cam-orphan"), None)
        assert entry is not None
        assert entry["status"] == "removed"


class TestRecordingsBulkDeleteContract:
    """DELETE bulk endpoints — by date and by camera."""

    def _seed(self, app, cam="cam-001", date="2026-04-11"):
        rec_dir = app.config["RECORDINGS_DIR"]
        clip_dir = os.path.join(rec_dir, cam, date)
        os.makedirs(clip_dir, exist_ok=True)
        for t in ("14-30-00", "15-00-00"):
            with open(os.path.join(clip_dir, f"{t}.mp4"), "wb") as f:
                f.write(b"\x00" * 128)

    def test_delete_date_success_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        self._seed(app)
        resp = client.delete("/api/v1/recordings/cam-001/2026-04-11")
        data = resp.get_json()
        _assert_fields(data, {"message", "count"})
        assert data["count"] == 2

    def test_delete_date_bad_date(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.delete("/api/v1/recordings/cam-001/not-a-date")
        data = resp.get_json()
        _assert_fields(data, {"error"})

    def test_delete_camera_success_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        self._seed(app, date="2026-04-11")
        self._seed(app, date="2026-04-12")
        resp = client.delete("/api/v1/recordings/cam-001")
        data = resp.get_json()
        _assert_fields(data, {"message", "count"})
        assert data["count"] == 4

    def test_delete_camera_not_found(self, app, client):
        _login(app, client)
        resp = client.delete("/api/v1/recordings/nope")
        data = resp.get_json()
        _assert_fields(data, {"error"})


# ===========================================================================
# Storage contracts (/api/v1/storage/*)
# ===========================================================================


class TestStorageStatusContract:
    """GET /api/v1/storage/status."""

    def test_fields(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/storage/status")
        data = resp.get_json()
        _assert_has_fields(
            data,
            {"total_gb", "used_gb", "free_gb", "percent", "recordings_dir"},
        )


class TestStorageDevicesContract:
    """GET /api/v1/storage/devices."""

    @patch("monitor.services.storage_service.usb.detect_devices")
    def test_fields(self, mock_detect, app, client):
        _login(app, client)
        mock_detect.return_value = [
            {
                "path": "/dev/sda1",
                "model": "USB",
                "size": "64G",
                "fstype": "ext4",
                "supported": True,
            },
        ]
        resp = client.get("/api/v1/storage/devices")
        data = resp.get_json()
        _assert_fields(data, {"devices"})
        assert isinstance(data["devices"], list)
        if data["devices"]:
            dev = data["devices"][0]
            _assert_has_fields(
                dev,
                {"path", "model", "size", "fstype", "supported"},
            )


# ===========================================================================
# OTA contracts (/api/v1/ota/*)
# ===========================================================================


class TestOtaStatusContract:
    """GET /api/v1/ota/status."""

    def test_fields(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/ota/status")
        data = resp.get_json()
        _assert_has_fields(data, {"server", "cameras"})
        _assert_has_fields(
            data["server"],
            {"current_version", "state"},
        )
        assert isinstance(data["cameras"], list)


# ===========================================================================
# Tailscale contracts (/api/v1/system/tailscale*)
# ===========================================================================


class TestOtaUsbScanContract:
    """GET /api/v1/ota/usb/scan."""

    def test_fields(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/ota/usb/scan")
        data = resp.get_json()
        _assert_has_fields(data, {"bundles"})
        assert isinstance(data["bundles"], list)

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        resp = client.get("/api/v1/ota/usb/scan")
        assert resp.status_code == 403


class TestOtaUsbImportContract:
    """POST /api/v1/ota/usb/import."""

    def test_requires_path(self, app, client):
        csrf = _login(app, client)
        resp = client.post(
            "/api/v1/ota/usb/import",
            json={},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        _assert_has_fields(data, {"error"})

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/ota/usb/import", json={"path": "/mnt/usb/x.swu"})
        assert resp.status_code == 403


class TestTailscaleStatusContract:
    """GET /api/v1/system/tailscale."""

    def test_fields(self, app, client):
        _login(app, client)
        resp = client.get("/api/v1/system/tailscale")
        data = resp.get_json()
        _assert_has_fields(
            data,
            {
                "installed",
                "running",
                "state",
                "hostname",
                "tailscale_ip",
                "peers",
                "config",
            },
        )
        assert isinstance(data["peers"], list)
        _assert_has_fields(
            data["config"],
            {"enabled", "auto_connect", "accept_routes", "ssh", "has_auth_key"},
        )

    def test_requires_auth(self, client):
        resp = client.get("/api/v1/system/tailscale")
        assert resp.status_code == 401


class TestTailscaleConnectContract:
    """POST /api/v1/system/tailscale/connect."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/system/tailscale/connect")
        assert resp.status_code == 403

    def test_requires_auth(self, client):
        resp = client.post("/api/v1/system/tailscale/connect")
        assert resp.status_code == 401


class TestTailscaleDisconnectContract:
    """POST /api/v1/system/tailscale/disconnect."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/system/tailscale/disconnect")
        assert resp.status_code == 403

    def test_requires_auth(self, client):
        resp = client.post("/api/v1/system/tailscale/disconnect")
        assert resp.status_code == 401


class TestTailscaleEnableContract:
    """POST /api/v1/system/tailscale/enable."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/system/tailscale/enable")
        assert resp.status_code == 403

    def test_requires_auth(self, client):
        resp = client.post("/api/v1/system/tailscale/enable")
        assert resp.status_code == 401


class TestTailscaleDisableContract:
    """POST /api/v1/system/tailscale/disable."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/system/tailscale/disable")
        assert resp.status_code == 403

    def test_requires_auth(self, client):
        resp = client.post("/api/v1/system/tailscale/disable")
        assert resp.status_code == 401


class TestTailscaleApplyConfigContract:
    """POST /api/v1/system/tailscale/apply-config."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/system/tailscale/apply-config")
        assert resp.status_code == 403

    def test_requires_auth(self, client):
        resp = client.post("/api/v1/system/tailscale/apply-config")
        assert resp.status_code == 401


# ===========================================================================
# Error response contracts (consistency check)
# ===========================================================================


class TestErrorResponseConsistency:
    """All error responses must use {"error": "..."} format."""

    def test_401_has_error_field(self, client):
        """Unauthenticated requests return {"error": "..."}."""
        resp = client.get("/api/v1/cameras")
        assert resp.status_code == 401
        data = resp.get_json()
        assert "error" in data
        assert isinstance(data["error"], str)

    def test_403_has_error_field(self, app, client):
        """Forbidden requests return {"error": "..."}."""
        _login(app, client, role="viewer")
        resp = client.post("/api/v1/users", json={"username": "x", "password": "y"})
        assert resp.status_code == 403
        data = resp.get_json()
        assert "error" in data

    def test_400_has_error_field(self, app, client):
        """Bad requests return {"error": "..."}."""
        resp = client.post("/api/v1/setup/wifi/save")
        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data


class TestConfigNotifyContract:
    """POST /api/v1/cameras/config-notify (HMAC auth from camera)."""

    def test_missing_headers_returns_401(self, app, client):
        _add_camera(app)
        resp = client.post(
            "/api/v1/cameras/config-notify",
            json={"fps": 15},
        )
        assert resp.status_code == 401
        data = resp.get_json()
        assert "error" in data

    def test_invalid_signature_returns_401(self, app, client):
        import time

        _add_camera(app, "cam-001")
        cam = app.store.get_camera("cam-001")
        cam.pairing_secret = "ab" * 32
        app.store.save_camera(cam)

        resp = client.post(
            "/api/v1/cameras/config-notify",
            json={"fps": 15},
            headers={
                "X-Camera-ID": "cam-001",
                "X-Timestamp": str(int(time.time())),
                "X-Signature": "bad_signature",
            },
        )
        assert resp.status_code == 401

    def test_valid_signature_returns_200(self, app, client):
        import hashlib
        import hmac
        import json
        import time

        _add_camera(app, "cam-001")
        cam = app.store.get_camera("cam-001")
        cam.pairing_secret = "ab" * 32
        app.store.save_camera(cam)

        body = json.dumps({"fps": 15}).encode()
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"cam-001:{timestamp}:{body_hash}"
        sig = hmac.new(
            bytes.fromhex("ab" * 32), message.encode(), hashlib.sha256
        ).hexdigest()

        resp = client.post(
            "/api/v1/cameras/config-notify",
            data=body,
            content_type="application/json",
            headers={
                "X-Camera-ID": "cam-001",
                "X-Timestamp": timestamp,
                "X-Signature": sig,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "message" in data


def _signed_camera_request(
    client, url, body_dict, camera_id="cam-001", secret="ab" * 32
):
    """Helper: build HMAC-signed POST to a camera machine-to-machine endpoint."""
    import hashlib
    import hmac
    import json
    import time

    body = json.dumps(body_dict).encode()
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{camera_id}:{timestamp}:{body_hash}"
    sig = hmac.new(bytes.fromhex(secret), message.encode(), hashlib.sha256).hexdigest()

    return client.post(
        url,
        data=body,
        content_type="application/json",
        headers={
            "X-Camera-ID": camera_id,
            "X-Timestamp": timestamp,
            "X-Signature": sig,
        },
    )


class TestHeartbeatContract:
    """Contract tests for POST /api/v1/cameras/heartbeat (ADR-0016)."""

    HEARTBEAT_URL = "/api/v1/cameras/heartbeat"
    SECRET = "ab" * 32

    def _payload(self, **overrides):
        data = {
            "streaming": True,
            "cpu_temp": 48.5,
            "memory_percent": 42,
            "uptime_seconds": 3600,
            "stream_config": {
                "width": 1920,
                "height": 1080,
                "fps": 25,
                "bitrate": 4000000,
                "h264_profile": "high",
                "keyframe_interval": 30,
                "rotation": 0,
                "hflip": False,
                "vflip": False,
            },
        }
        data.update(overrides)
        return data

    def test_missing_headers_returns_401(self, app, client):
        resp = client.post(self.HEARTBEAT_URL, json=self._payload())
        assert resp.status_code == 401

    def test_unknown_camera_returns_401(self, app, client):
        import time

        resp = client.post(
            self.HEARTBEAT_URL,
            json=self._payload(),
            headers={
                "X-Camera-ID": "cam-unknown",
                "X-Timestamp": str(int(time.time())),
                "X-Signature": "bad",
            },
        )
        assert resp.status_code == 401

    def test_invalid_signature_returns_401(self, app, client):
        import time

        _add_camera(app, "cam-001")
        cam = app.store.get_camera("cam-001")
        cam.pairing_secret = self.SECRET
        app.store.save_camera(cam)

        resp = client.post(
            self.HEARTBEAT_URL,
            json=self._payload(),
            headers={
                "X-Camera-ID": "cam-001",
                "X-Timestamp": str(int(time.time())),
                "X-Signature": "invalid",
            },
        )
        assert resp.status_code == 401

    def test_valid_heartbeat_returns_200_ok(self, app, client):
        _add_camera(app, "cam-001")
        cam = app.store.get_camera("cam-001")
        cam.pairing_secret = self.SECRET
        app.store.save_camera(cam)

        resp = _signed_camera_request(
            client, self.HEARTBEAT_URL, self._payload(), secret=self.SECRET
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True

    def test_heartbeat_updates_camera_status_to_online(self, app, client):
        _add_camera(app, "cam-001", status="offline")
        cam = app.store.get_camera("cam-001")
        cam.pairing_secret = self.SECRET
        app.store.save_camera(cam)

        _signed_camera_request(
            client, self.HEARTBEAT_URL, self._payload(), secret=self.SECRET
        )

        updated = app.store.get_camera("cam-001")
        assert updated.status == "online"
        assert updated.streaming is True

    def test_heartbeat_updates_health_metrics(self, app, client):
        _add_camera(app, "cam-001")
        cam = app.store.get_camera("cam-001")
        cam.pairing_secret = self.SECRET
        app.store.save_camera(cam)

        _signed_camera_request(
            client,
            self.HEARTBEAT_URL,
            self._payload(cpu_temp=60.0, memory_percent=75, uptime_seconds=900),
            secret=self.SECRET,
        )

        updated = app.store.get_camera("cam-001")
        assert updated.cpu_temp == 60.0
        assert updated.memory_percent == 75
        assert updated.uptime_seconds == 900

    def test_heartbeat_returns_pending_config_when_needed(self, app, client):
        _add_camera(app, "cam-001")
        cam = app.store.get_camera("cam-001")
        cam.pairing_secret = self.SECRET
        cam.config_sync = "pending"
        cam.fps = 30
        app.store.save_camera(cam)

        resp = _signed_camera_request(
            client, self.HEARTBEAT_URL, self._payload(), secret=self.SECRET
        )
        data = resp.get_json()
        assert "pending_config" in data
        assert data["pending_config"]["fps"] == 30

    def test_expired_timestamp_returns_401(self, app, client):
        import hashlib
        import hmac
        import json
        import time

        _add_camera(app, "cam-001")
        cam = app.store.get_camera("cam-001")
        cam.pairing_secret = self.SECRET
        app.store.save_camera(cam)

        body_dict = self._payload()
        body = json.dumps(body_dict).encode()
        old_ts = str(int(time.time()) - 400)  # 400s ago — beyond 300s window
        body_hash = hashlib.sha256(body).hexdigest()
        message = f"cam-001:{old_ts}:{body_hash}"
        sig = hmac.new(
            bytes.fromhex(self.SECRET), message.encode(), hashlib.sha256
        ).hexdigest()

        resp = client.post(
            self.HEARTBEAT_URL,
            data=body,
            content_type="application/json",
            headers={
                "X-Camera-ID": "cam-001",
                "X-Timestamp": old_ts,
                "X-Signature": sig,
            },
        )
        assert resp.status_code == 401


# ===========================================================================
# ADR-0017: recording-mode fields + on-demand endpoints
# ===========================================================================


class TestCameraUpdateRecordingModeContract:
    """PUT /api/v1/cameras/<id> accepts new recording_* fields."""

    def test_put_accepts_schedule_mode(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.put(
            "/api/v1/cameras/cam-001",
            json={
                "recording_mode": "schedule",
                "recording_schedule": [
                    {"days": ["mon", "tue"], "start": "09:00", "end": "17:00"}
                ],
            },
        )
        assert resp.status_code == 200
        cam = app.store.get_camera("cam-001")
        assert cam.recording_mode == "schedule"
        assert cam.recording_schedule[0]["start"] == "09:00"

    def test_put_rejects_invalid_mode(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.put("/api/v1/cameras/cam-001", json={"recording_mode": "nope"})
        assert resp.status_code == 400

    def test_put_rejects_bad_schedule_day(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.put(
            "/api/v1/cameras/cam-001",
            json={
                "recording_mode": "schedule",
                "recording_schedule": [
                    {"days": ["funday"], "start": "09:00", "end": "17:00"}
                ],
            },
        )
        assert resp.status_code == 400

    def test_put_rejects_bad_time_format(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.put(
            "/api/v1/cameras/cam-001",
            json={
                "recording_mode": "schedule",
                "recording_schedule": [{"days": ["mon"], "start": "9am", "end": "5pm"}],
            },
        )
        assert resp.status_code == 400

    def test_put_motion_accepted_as_forward_compat(self, app, client):
        _login(app, client)
        _add_camera(app)
        resp = client.put("/api/v1/cameras/cam-001", json={"recording_mode": "motion"})
        assert resp.status_code == 200


class TestOnDemandEndpointContract:
    """Internal-only coordinator shape (marked x-internal in OpenAPI)."""

    def test_start_shape(self, app, client):
        from unittest.mock import MagicMock

        _add_camera(app)
        app.camera_control_client = MagicMock()
        app.camera_control_client.start_stream.return_value = ({"state": "running"}, "")

        resp = client.post("/internal/on-demand/cam-001/start")
        assert resp.status_code == 200
        data = resp.get_json()
        _assert_has_fields(data, {"ok"})

    def test_non_localhost_forbidden(self, app, client):
        _add_camera(app)
        client.environ_base["REMOTE_ADDR"] = "10.0.0.5"
        resp = client.post("/internal/on-demand/cam-001/start")
        assert resp.status_code == 403
