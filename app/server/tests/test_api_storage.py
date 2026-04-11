"""Tests for the storage API.

Tests exercise the HTTP endpoints which now delegate to StorageService.
Mocks target the service's dependency (monitor.services.storage_service.usb)
rather than the old route-level import.
"""

from unittest.mock import MagicMock, patch

from monitor.auth import hash_password


def _login(app, client, role="admin"):
    """Helper: create admin user and login."""
    from monitor.models import User

    app.store.save_user(
        User(
            id="user-admin",
            username="admin",
            password_hash=hash_password("pass"),
            role=role,
        )
    )
    client.post(
        "/api/v1/auth/login",
        json={
            "username": "admin",
            "password": "pass",
        },
    )


def _make_device(
    path="/dev/sda1", model="USB Stick", size="32G", fstype="ext4", supported=True
):
    """Build a fake USB device dict."""
    return {
        "path": path,
        "model": model,
        "size": size,
        "fstype": fstype,
        "supported": supported,
    }


# Patch target: StorageService imports usb at module level
USB_PATCH = "monitor.services.storage_service.usb"


class TestGetStatus:
    """Test GET /api/v1/storage/status."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/storage/status").status_code == 401

    def test_returns_storage_stats(self, app, client):
        _login(app, client)
        mock_stats = {
            "total_bytes": 100000000,
            "used_bytes": 40000000,
            "free_bytes": 60000000,
            "recordings_dir": "/data/recordings",
        }
        # Replace the storage_manager inside the service
        app.storage_service._storage_manager = MagicMock()
        app.storage_service._storage_manager.get_storage_stats.return_value = mock_stats

        response = client.get("/api/v1/storage/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["total_bytes"] == 100000000
        assert data["recordings_dir"] == "/data/recordings"

    def test_returns_500_when_no_storage_manager(self, app, client):
        _login(app, client)
        # Set storage_manager to None inside service
        app.storage_service._storage_manager = None

        response = client.get("/api/v1/storage/status")
        assert response.status_code == 500
        assert "not initialized" in response.get_json()["error"]


class TestListDevices:
    """Test GET /api/v1/storage/devices."""

    def test_requires_auth(self, client):
        assert client.get("/api/v1/storage/devices").status_code == 401

    @patch(USB_PATCH)
    def test_returns_device_list(self, mock_usb, app, client):
        _login(app, client)
        devices = [
            _make_device("/dev/sda1"),
            _make_device("/dev/sdb1", model="Flash Drive"),
        ]
        mock_usb.detect_devices.return_value = devices

        response = client.get("/api/v1/storage/devices")
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["devices"]) == 2
        assert data["devices"][0]["path"] == "/dev/sda1"

    @patch(USB_PATCH)
    def test_returns_empty_list(self, mock_usb, app, client):
        _login(app, client)
        mock_usb.detect_devices.return_value = []

        response = client.get("/api/v1/storage/devices")
        assert response.status_code == 200
        assert response.get_json()["devices"] == []


class TestSelectDevice:
    """Test POST /api/v1/storage/select."""

    @patch(USB_PATCH)
    def test_select_valid_device(self, mock_usb, app, client):
        _login(app, client)
        device = _make_device("/dev/sda1")
        mock_usb.detect_devices.return_value = [device]
        mock_usb.mount_device.return_value = (True, None)
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"

        app.storage_service._storage_manager = MagicMock()

        response = client.post(
            "/api/v1/storage/select",
            json={
                "device_path": "/dev/sda1",
            },
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["recordings_dir"] == "/mnt/usb/recordings"
        assert data["device"]["path"] == "/dev/sda1"

        mock_usb.mount_device.assert_called_once_with("/dev/sda1")
        mock_usb.prepare_recordings_dir.assert_called_once()
        app.storage_service._storage_manager.set_recordings_dir.assert_called_once_with(
            "/mnt/usb/recordings"
        )

    def test_missing_device_path(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/storage/select", json={"device_path": ""})
        assert response.status_code == 400
        assert "device_path required" in response.get_json()["error"]

    def test_missing_json_body(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/storage/select")
        assert response.status_code == 400

    @patch(USB_PATCH)
    def test_device_not_found(self, mock_usb, app, client):
        _login(app, client)
        mock_usb.detect_devices.return_value = []

        response = client.post(
            "/api/v1/storage/select",
            json={
                "device_path": "/dev/sda1",
            },
        )
        assert response.status_code == 404
        assert "not found" in response.get_json()["error"]

    @patch(USB_PATCH)
    def test_unsupported_filesystem(self, mock_usb, app, client):
        _login(app, client)
        device = _make_device("/dev/sda1", fstype="ntfs", supported=False)
        mock_usb.detect_devices.return_value = [device]

        response = client.post(
            "/api/v1/storage/select",
            json={
                "device_path": "/dev/sda1",
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data["needs_format"] is True
        assert data["fstype"] == "ntfs"

    @patch(USB_PATCH)
    def test_mount_failure(self, mock_usb, app, client):
        _login(app, client)
        device = _make_device("/dev/sda1")
        mock_usb.detect_devices.return_value = [device]
        mock_usb.mount_device.return_value = (False, "mount: permission denied")

        response = client.post(
            "/api/v1/storage/select",
            json={
                "device_path": "/dev/sda1",
            },
        )
        assert response.status_code == 500
        assert "Failed to mount" in response.get_json()["error"]

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        response = client.post(
            "/api/v1/storage/select",
            json={
                "device_path": "/dev/sda1",
            },
        )
        assert response.status_code == 403


class TestFormatDevice:
    """Test POST /api/v1/storage/format."""

    @patch(USB_PATCH)
    def test_format_success(self, mock_usb, app, client):
        _login(app, client)
        device = _make_device("/dev/sda1", fstype="ntfs", supported=False)
        mock_usb.detect_devices.return_value = [device]
        mock_usb.format_device.return_value = (True, None)

        response = client.post(
            "/api/v1/storage/format",
            json={
                "device_path": "/dev/sda1",
                "confirm": True,
            },
        )
        assert response.status_code == 200
        assert "formatted" in response.get_json()["message"].lower()
        mock_usb.format_device.assert_called_once_with("/dev/sda1")

    def test_format_without_confirm(self, app, client):
        _login(app, client)
        response = client.post(
            "/api/v1/storage/format",
            json={
                "device_path": "/dev/sda1",
                "confirm": False,
            },
        )
        assert response.status_code == 400
        data = response.get_json()
        assert data["needs_confirmation"] is True

    def test_format_missing_confirm(self, app, client):
        _login(app, client)
        response = client.post(
            "/api/v1/storage/format",
            json={
                "device_path": "/dev/sda1",
            },
        )
        assert response.status_code == 400
        assert "needs_confirmation" in response.get_json()

    @patch(USB_PATCH)
    def test_format_device_not_found(self, mock_usb, app, client):
        _login(app, client)
        mock_usb.detect_devices.return_value = []

        response = client.post(
            "/api/v1/storage/format",
            json={
                "device_path": "/dev/sda1",
                "confirm": True,
            },
        )
        assert response.status_code == 404
        assert "not found" in response.get_json()["error"]

    @patch(USB_PATCH)
    def test_format_failure(self, mock_usb, app, client):
        _login(app, client)
        device = _make_device("/dev/sda1")
        mock_usb.detect_devices.return_value = [device]
        mock_usb.format_device.return_value = (False, "mkfs failed")

        response = client.post(
            "/api/v1/storage/format",
            json={
                "device_path": "/dev/sda1",
                "confirm": True,
            },
        )
        assert response.status_code == 500
        assert "Format failed" in response.get_json()["error"]

    def test_format_missing_device_path(self, app, client):
        _login(app, client)
        response = client.post(
            "/api/v1/storage/format",
            json={
                "confirm": True,
            },
        )
        assert response.status_code == 400
        assert "device_path required" in response.get_json()["error"]

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        response = client.post(
            "/api/v1/storage/format",
            json={
                "device_path": "/dev/sda1",
                "confirm": True,
            },
        )
        assert response.status_code == 403


class TestEjectDevice:
    """Test POST /api/v1/storage/eject."""

    @patch(USB_PATCH)
    def test_eject_success(self, mock_usb, app, client):
        _login(app, client)
        mock_usb.unmount_device.return_value = (True, None)

        app.storage_service._storage_manager = MagicMock()

        response = client.post("/api/v1/storage/eject")
        assert response.status_code == 200
        data = response.get_json()
        assert "internal storage" in data["message"].lower()
        assert data["recordings_dir"] == app.config["RECORDINGS_DIR"]

        mock_usb.unmount_device.assert_called_once()
        app.storage_service._storage_manager.set_recordings_dir.assert_called_once_with(
            app.config["RECORDINGS_DIR"]
        )

    @patch(USB_PATCH)
    def test_eject_unmount_warning(self, mock_usb, app, client):
        """Eject still succeeds even if unmount has a warning."""
        _login(app, client)
        mock_usb.unmount_device.return_value = (False, "device busy")

        app.storage_service._storage_manager = MagicMock()

        response = client.post("/api/v1/storage/eject")
        # Eject still returns 200 — unmount failure is just a warning
        assert response.status_code == 200

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        response = client.post("/api/v1/storage/eject")
        assert response.status_code == 403

    def test_requires_auth(self, client):
        assert client.post("/api/v1/storage/eject").status_code == 401
