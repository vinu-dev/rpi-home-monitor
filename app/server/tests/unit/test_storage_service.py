"""Tests for StorageService — USB storage orchestration layer.

Tests cover all public methods: get_status, list_devices, select_device,
format_device, eject, plus audit logging and config persistence behaviors.
Mocks target the module-level usb import (monitor.services.storage_service.usb).
"""

from unittest.mock import MagicMock, patch

from monitor.services.storage_service import StorageService

# Patch target: StorageService imports usb at module level
USB_PATCH = "monitor.services.storage_service.usb"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_device(
    path="/dev/sda1", model="USB Stick", size="32G", fstype="ext4", supported=True
):
    """Build a fake USB device dict matching usb.detect_devices() output."""
    return {
        "path": path,
        "model": model,
        "size": size,
        "fstype": fstype,
        "supported": supported,
    }


def _make_service(
    storage_manager=None, store=None, audit=None, default_dir="/data/recordings"
):
    """Create a StorageService with sensible mock defaults."""
    if store is None:
        store = MagicMock()
        store.get_settings.return_value = MagicMock(
            usb_device="",
            usb_recordings_dir="",
        )
    if storage_manager is None:
        storage_manager = MagicMock()
    return StorageService(
        storage_manager=storage_manager,
        store=store,
        audit=audit,
        default_recordings_dir=default_dir,
    )


# ---------------------------------------------------------------------------
# 1. get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_returns_stats_from_manager(self):
        mgr = MagicMock()
        mgr.get_storage_stats.return_value = {"total_gb": 50, "free_gb": 30}
        svc = _make_service(storage_manager=mgr)

        stats, err = svc.get_status()

        assert stats == {"total_gb": 50, "free_gb": 30}
        assert err == ""
        mgr.get_storage_stats.assert_called_once()

    def test_returns_error_when_manager_is_none(self):
        svc = StorageService(
            storage_manager=None,
            store=MagicMock(),
            audit=None,
        )

        stats, err = svc.get_status()

        assert stats is None
        assert "not initialized" in err


# ---------------------------------------------------------------------------
# 2. list_devices
# ---------------------------------------------------------------------------


class TestListDevices:
    @patch(USB_PATCH)
    def test_delegates_to_usb_detect(self, mock_usb):
        devices = [_make_device("/dev/sda1"), _make_device("/dev/sdb1")]
        mock_usb.detect_devices.return_value = devices
        svc = _make_service()

        result = svc.list_devices()

        assert result == devices
        mock_usb.detect_devices.assert_called_once()

    @patch(USB_PATCH)
    def test_returns_empty_list_when_no_devices(self, mock_usb):
        mock_usb.detect_devices.return_value = []
        svc = _make_service()

        assert svc.list_devices() == []


# ---------------------------------------------------------------------------
# 3. select_device — validation
# ---------------------------------------------------------------------------


class TestSelectDeviceValidation:
    def test_missing_device_path_returns_400(self):
        svc = _make_service()

        result, err, status = svc.select_device("")

        assert result is None
        assert "device_path required" in err
        assert status == 400

    @patch(USB_PATCH)
    def test_device_not_found_returns_404(self, mock_usb):
        mock_usb.detect_devices.return_value = []
        svc = _make_service()

        result, err, status = svc.select_device("/dev/sda1")

        assert result is None
        assert "not found" in err
        assert status == 404

    @patch(USB_PATCH)
    def test_unsupported_filesystem_returns_400_with_needs_format(self, mock_usb):
        mock_usb.detect_devices.return_value = [
            _make_device(fstype="ntfs", supported=False),
        ]
        svc = _make_service()

        result, err, status = svc.select_device("/dev/sda1")

        assert status == 400
        assert result["needs_format"] is True
        assert result["fstype"] == "ntfs"
        assert "not supported" in err

    @patch(USB_PATCH)
    def test_mount_failure_returns_500(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (False, "mount: busy")
        svc = _make_service()

        result, err, status = svc.select_device("/dev/sda1")

        assert result is None
        assert "Failed to mount" in err
        assert status == 500


# ---------------------------------------------------------------------------
# 4. select_device — success path
# ---------------------------------------------------------------------------


class TestSelectDeviceSuccess:
    @patch(USB_PATCH)
    def test_success_returns_200_with_device_info(self, mock_usb):
        device = _make_device(model="SanDisk", size="64G")
        mock_usb.detect_devices.return_value = [device]
        mock_usb.mount_device.return_value = (True, "")
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"
        svc = _make_service()

        result, err, status = svc.select_device("/dev/sda1")

        assert status == 200
        assert err == ""
        assert "SanDisk" in result["message"]
        assert "64G" in result["message"]
        assert result["recordings_dir"] == "/mnt/usb/recordings"
        assert result["device"] == device

    @patch(USB_PATCH)
    def test_success_updates_storage_manager(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (True, "")
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"
        mgr = MagicMock()
        svc = _make_service(storage_manager=mgr)

        svc.select_device("/dev/sda1")

        mgr.set_recordings_dir.assert_called_once_with("/mnt/usb/recordings")

    @patch(USB_PATCH)
    def test_success_saves_config(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (True, "")
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"
        store = MagicMock()
        settings = MagicMock(usb_device="", usb_recordings_dir="")
        store.get_settings.return_value = settings
        svc = _make_service(store=store)

        svc.select_device("/dev/sda1")

        assert settings.usb_device == "/dev/sda1"
        assert settings.usb_recordings_dir == "/mnt/usb/recordings"
        store.save_settings.assert_called_once_with(settings)

    @patch(USB_PATCH)
    def test_success_logs_audit(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (True, "")
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"
        audit = MagicMock()
        svc = _make_service(audit=audit)

        svc.select_device("/dev/sda1", user="admin", ip="10.0.0.1")

        audit.log_event.assert_called_once()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "USB_STORAGE_SELECTED"
        assert call_args[1]["user"] == "admin"
        assert call_args[1]["ip"] == "10.0.0.1"


# ---------------------------------------------------------------------------
# 5. format_device
# ---------------------------------------------------------------------------


class TestFormatDevice:
    def test_missing_device_path_returns_400(self):
        svc = _make_service()

        msg, status = svc.format_device("")

        assert "device_path required" in msg
        assert status == 400

    def test_no_confirm_returns_400_warning(self):
        svc = _make_service()

        msg, status = svc.format_device("/dev/sda1", confirm=False)

        assert status == 400
        assert "confirm=true" in msg
        assert "ERASE ALL DATA" in msg

    @patch(USB_PATCH)
    def test_device_not_found_returns_404(self, mock_usb):
        mock_usb.detect_devices.return_value = []
        svc = _make_service()

        msg, status = svc.format_device("/dev/sda1", confirm=True)

        assert status == 404
        assert "not found" in msg

    @patch(USB_PATCH)
    def test_format_failure_returns_500(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.format_device.return_value = (False, "permission denied")
        svc = _make_service()

        msg, status = svc.format_device("/dev/sda1", confirm=True)

        assert status == 500
        assert "Format failed" in msg

    @patch(USB_PATCH)
    def test_format_success_returns_200(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.format_device.return_value = (True, "")
        svc = _make_service()

        msg, status = svc.format_device("/dev/sda1", confirm=True)

        assert status == 200
        assert "formatted as ext4" in msg

    @patch(USB_PATCH)
    def test_format_logs_audit(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device(model="Kingston")]
        mock_usb.format_device.return_value = (True, "")
        audit = MagicMock()
        svc = _make_service(audit=audit)

        svc.format_device("/dev/sda1", confirm=True, user="admin", ip="10.0.0.1")

        audit.log_event.assert_called_once()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "USB_FORMAT"
        assert "Kingston" in call_args[1]["detail"]


# ---------------------------------------------------------------------------
# 6. eject
# ---------------------------------------------------------------------------


class TestEject:
    @patch(USB_PATCH)
    def test_eject_switches_to_internal_storage(self, mock_usb):
        mock_usb.unmount_device.return_value = (True, "")
        mgr = MagicMock()
        svc = _make_service(storage_manager=mgr, default_dir="/data/recordings")

        svc.eject()

        mgr.set_recordings_dir.assert_called_once_with("/data/recordings")

    @patch(USB_PATCH)
    def test_eject_unmounts_device(self, mock_usb):
        mock_usb.unmount_device.return_value = (True, "")
        svc = _make_service()

        svc.eject()

        mock_usb.unmount_device.assert_called_once()

    @patch(USB_PATCH)
    def test_eject_returns_200_with_internal_dir(self, mock_usb):
        mock_usb.unmount_device.return_value = (True, "")
        svc = _make_service(default_dir="/data/recordings")

        result, err, status = svc.eject()

        assert status == 200
        assert err == ""
        assert result["recordings_dir"] == "/data/recordings"
        assert "ejected" in result["message"].lower()

    @patch(USB_PATCH)
    def test_eject_clears_usb_config(self, mock_usb):
        mock_usb.unmount_device.return_value = (True, "")
        store = MagicMock()
        settings = MagicMock(usb_device="/dev/sda1", usb_recordings_dir="/mnt/usb/rec")
        store.get_settings.return_value = settings
        svc = _make_service(store=store)

        svc.eject()

        assert settings.usb_device == ""
        assert settings.usb_recordings_dir == ""
        store.save_settings.assert_called_once()

    @patch(USB_PATCH)
    def test_eject_unmount_failure_does_not_fail_operation(self, mock_usb):
        mock_usb.unmount_device.return_value = (False, "device busy")
        svc = _make_service()

        result, err, status = svc.eject()

        assert status == 200
        assert "ejected" in result["message"].lower()

    @patch(USB_PATCH)
    def test_eject_logs_audit(self, mock_usb):
        mock_usb.unmount_device.return_value = (True, "")
        audit = MagicMock()
        svc = _make_service(audit=audit)

        svc.eject(user="admin", ip="10.0.0.1")

        audit.log_event.assert_called_once()
        call_args = audit.log_event.call_args
        assert call_args[0][0] == "USB_STORAGE_EJECTED"
        assert call_args[1]["user"] == "admin"


# ---------------------------------------------------------------------------
# 7. Audit failure resilience
# ---------------------------------------------------------------------------


class TestAuditFailureResilience:
    @patch(USB_PATCH)
    def test_audit_exception_does_not_break_select(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (True, "")
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("audit db locked")
        svc = _make_service(audit=audit)

        result, err, status = svc.select_device("/dev/sda1")

        assert status == 200

    @patch(USB_PATCH)
    def test_audit_exception_does_not_break_eject(self, mock_usb):
        mock_usb.unmount_device.return_value = (True, "")
        audit = MagicMock()
        audit.log_event.side_effect = RuntimeError("audit db locked")
        svc = _make_service(audit=audit)

        result, err, status = svc.eject()

        assert status == 200

    @patch(USB_PATCH)
    def test_no_audit_provided_does_not_crash(self, mock_usb):
        mock_usb.unmount_device.return_value = (True, "")
        svc = _make_service(audit=None)

        result, err, status = svc.eject(user="admin")

        assert status == 200


# ---------------------------------------------------------------------------
# 8. Config persistence failure resilience
# ---------------------------------------------------------------------------


class TestConfigPersistenceFailure:
    @patch(USB_PATCH)
    def test_save_settings_failure_does_not_break_select(self, mock_usb):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (True, "")
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"
        store = MagicMock()
        store.get_settings.side_effect = OSError("disk full")
        svc = _make_service(store=store)

        result, err, status = svc.select_device("/dev/sda1")

        assert status == 200
