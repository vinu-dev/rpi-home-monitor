"""
API contract tests — verify exact response field names for every endpoint.

These tests catch silent API drift: a renamed field won't break server-side
tests but will break the frontend. Contract tests make field names explicit.

Layer 4 of the testing pyramid (see docs/development-guide.md Section 3.8).
"""

import os
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

CAMERA_LIST_FIELDS = {
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
}


class TestCamerasListContract:
    """GET /api/v1/cameras — array of camera objects."""

    def test_camera_object_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        data = client.get("/api/v1/cameras").get_json()
        assert isinstance(data, list)
        assert len(data) >= 1
        _assert_fields(data[0], CAMERA_LIST_FIELDS)

    def test_excludes_sensitive_fields(self, app, client):
        _login(app, client)
        _add_camera(app)
        data = client.get("/api/v1/cameras").get_json()
        cam = data[0]
        for field in ["rtsp_url", "cert_serial", "password"]:
            assert field not in cam, f"Sensitive field '{field}' leaked"


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
        _assert_fields(data, {"hostname", "firmware_version", "uptime"})


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
