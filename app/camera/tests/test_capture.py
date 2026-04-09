"""Tests for camera_streamer.capture module."""
import os
import pytest
from unittest.mock import patch, MagicMock

from camera_streamer.capture import CaptureManager


class TestCaptureManager:
    """Test camera device validation."""

    def test_device_not_found(self, tmp_path):
        """Should return False when device doesn't exist."""
        mgr = CaptureManager(device=str(tmp_path / "nonexistent"))
        assert mgr.check() is False
        assert mgr.available is False

    def test_device_found(self, tmp_path):
        """Should return True when device exists (even if not real v4l2)."""
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")  # Not a real char device but exists
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Format: H.264\n  1920x1080\n  1280x720\n",
            )
            mgr = CaptureManager(device=str(fake_dev))
            assert mgr.check() is True
            assert mgr.available is True

    def test_formats_populated(self, tmp_path):
        """Should populate formats from v4l2-ctl output."""
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="[0]: 'H264' (H.264)\n  Size: 1920x1080\n  Size: 1280x720\n",
            )
            mgr = CaptureManager(device=str(fake_dev))
            mgr.check()
            assert len(mgr.formats) > 0

    def test_supports_h264(self, tmp_path):
        """Should detect H.264 support."""
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="H.264\n1920x1080\n",
            )
            mgr = CaptureManager(device=str(fake_dev))
            mgr.check()
            assert mgr.supports_h264() is True

    def test_supports_resolution(self, tmp_path):
        """Should detect resolution support."""
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="1920x1080\n1280x720\n",
            )
            mgr = CaptureManager(device=str(fake_dev))
            mgr.check()
            assert mgr.supports_resolution(1920, 1080) is True
            assert mgr.supports_resolution(3840, 2160) is False

    def test_v4l2ctl_not_found(self, tmp_path):
        """Should handle missing v4l2-ctl gracefully."""
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            mgr = CaptureManager(device=str(fake_dev))
            assert mgr.check() is True  # Device exists, just can't query
            assert mgr.formats == []

    def test_v4l2ctl_timeout(self, tmp_path):
        """Should handle v4l2-ctl timeout gracefully."""
        import subprocess
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
            mgr = CaptureManager(device=str(fake_dev))
            assert mgr.check() is True
            assert mgr.formats == []

    def test_default_device(self):
        """Default device should be /dev/video0."""
        mgr = CaptureManager()
        assert mgr.device == "/dev/video0"
