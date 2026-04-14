"""Unit tests for StorageService — USB storage orchestration layer."""

from unittest.mock import MagicMock, patch

import pytest

from monitor.services.storage_service import StorageService

USB_PATCH = "monitor.services.storage_service.usb"


def _make_device(
    path="/dev/sda1", model="USB Stick", size="32G", fstype="ext4", supported=True
):
    return {
        "path": path,
        "model": model,
        "size": size,
        "fstype": fstype,
        "supported": supported,
    }


@pytest.fixture
def deps():
    """Return (storage_manager, store, audit) mocks."""
    sm = MagicMock()
    store = MagicMock()
    audit = MagicMock()
    return sm, store, audit


@pytest.fixture
def svc(deps):
    sm, store, audit = deps
    return StorageService(sm, store, audit, default_recordings_dir="/data/recordings")


class TestGetStatus:
    def test_returns_stats(self, svc, deps):
        sm, _, _ = deps
        sm.get_storage_stats.return_value = {"total_bytes": 100}
        stats, err = svc.get_status()
        assert stats == {"total_bytes": 100}
        assert err == ""

    def test_error_when_no_storage_manager(self, deps):
        _, store, audit = deps
        svc = StorageService(None, store, audit)
        stats, err = svc.get_status()
        assert stats is None
        assert "not initialized" in err


class TestListDevices:
    @patch(USB_PATCH)
    def test_delegates_to_usb(self, mock_usb, svc):
        mock_usb.detect_devices.return_value = [_make_device()]
        result = svc.list_devices()
        assert len(result) == 1
        mock_usb.detect_devices.assert_called_once()


class TestSelectDevice:
    def test_missing_device_path(self, svc):
        result, err, status = svc.select_device("")
        assert status == 400
        assert "device_path required" in err

    @patch(USB_PATCH)
    def test_device_not_found(self, mock_usb, svc):
        mock_usb.detect_devices.return_value = []
        _, err, status = svc.select_device("/dev/sda1")
        assert status == 404
        assert "not found" in err

    @patch(USB_PATCH)
    def test_unsupported_filesystem(self, mock_usb, svc):
        mock_usb.detect_devices.return_value = [
            _make_device(fstype="ntfs", supported=False)
        ]
        result, err, status = svc.select_device("/dev/sda1")
        assert status == 400
        assert result["needs_format"] is True
        assert result["fstype"] == "ntfs"

    @patch(USB_PATCH)
    def test_mount_failure(self, mock_usb, svc):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (False, "permission denied")
        _, err, status = svc.select_device("/dev/sda1")
        assert status == 500
        assert "Failed to mount" in err

    @patch(USB_PATCH)
    def test_success(self, mock_usb, svc, deps):
        sm, store, audit = deps
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (True, None)
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"

        result, err, status = svc.select_device("/dev/sda1", user="admin", ip="1.2.3.4")
        assert status == 200
        assert err == ""
        assert result["recordings_dir"] == "/mnt/usb/recordings"
        sm.set_recordings_dir.assert_called_once_with("/mnt/usb/recordings")
        audit.log_event.assert_called_once()

    @patch(USB_PATCH)
    def test_saves_usb_config(self, mock_usb, svc, deps):
        _, store, _ = deps
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (True, None)
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"

        svc.select_device("/dev/sda1")
        store.get_settings.assert_called()
        store.save_settings.assert_called()


class TestFormatDevice:
    def test_missing_device_path(self, svc):
        msg, status = svc.format_device("")
        assert status == 400
        assert "device_path required" in msg

    def test_missing_confirm(self, svc):
        msg, status = svc.format_device("/dev/sda1", confirm=False)
        assert status == 400
        assert "confirm" in msg.lower()

    @patch(USB_PATCH)
    def test_device_not_found(self, mock_usb, svc):
        mock_usb.detect_devices.return_value = []
        msg, status = svc.format_device("/dev/sda1", confirm=True)
        assert status == 404
        assert "not found" in msg

    @patch(USB_PATCH)
    def test_format_failure(self, mock_usb, svc):
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.format_device.return_value = (False, "mkfs failed")
        msg, status = svc.format_device("/dev/sda1", confirm=True)
        assert status == 500
        assert "Format failed" in msg

    @patch(USB_PATCH)
    def test_format_success(self, mock_usb, svc, deps):
        _, _, audit = deps
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.format_device.return_value = (True, None)
        msg, status = svc.format_device("/dev/sda1", confirm=True, user="admin")
        assert status == 200
        assert "formatted" in msg.lower()
        audit.log_event.assert_called_once()


class TestEject:
    @patch(USB_PATCH)
    def test_eject_success(self, mock_usb, svc, deps):
        sm, store, audit = deps
        mock_usb.unmount_device.return_value = (True, None)
        result, err, status = svc.eject(user="admin")
        assert status == 200
        assert err == ""
        assert result["recordings_dir"] == "/data/recordings"
        sm.set_recordings_dir.assert_called_once_with("/data/recordings")
        audit.log_event.assert_called_once()

    @patch(USB_PATCH)
    def test_eject_unmount_warning_still_succeeds(self, mock_usb, svc):
        mock_usb.unmount_device.return_value = (False, "device busy")
        result, err, status = svc.eject()
        assert status == 200

    @patch(USB_PATCH)
    def test_eject_clears_usb_config(self, mock_usb, svc, deps):
        _, store, _ = deps
        mock_usb.unmount_device.return_value = (True, None)
        svc.eject()
        settings = store.get_settings.return_value
        assert settings.usb_device == ""
        assert settings.usb_recordings_dir == ""
        store.save_settings.assert_called()


class TestAuditFailSilent:
    @patch(USB_PATCH)
    def test_audit_error_doesnt_break_select(self, mock_usb, deps):
        sm, store, audit = deps
        audit.log_event.side_effect = RuntimeError("log failed")
        svc = StorageService(sm, store, audit)
        mock_usb.detect_devices.return_value = [_make_device()]
        mock_usb.mount_device.return_value = (True, None)
        mock_usb.prepare_recordings_dir.return_value = "/mnt/usb/recordings"
        mock_usb.DEFAULT_MOUNT_POINT = "/mnt/usb"

        result, err, status = svc.select_device("/dev/sda1", user="admin")
        assert status == 200

    def test_no_audit_logger(self):
        svc = StorageService(MagicMock(), MagicMock(), audit=None)
        # Should not raise
        svc._log_audit("TEST", "user", "ip", "detail")
