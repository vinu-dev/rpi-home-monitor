"""Tests for camera_streamer.discovery module."""
import pytest
from unittest.mock import patch, MagicMock

from camera_streamer.discovery import DiscoveryService, SERVICE_TYPE, SERVICE_PORT


class TestDiscoveryService:
    """Test mDNS advertisement via Avahi."""

    def test_not_advertising_initially(self, camera_config):
        """Should not be advertising before start()."""
        svc = DiscoveryService(camera_config)
        assert svc.is_advertising is False

    def test_start_launches_avahi_publish(self, camera_config):
        """start() should launch avahi-publish-service."""
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None
            mock_popen.return_value = proc

            svc = DiscoveryService(camera_config)
            svc.start()
            assert svc.is_advertising is True

            # Verify command
            args = mock_popen.call_args[0][0]
            assert args[0] == "avahi-publish-service"
            assert SERVICE_TYPE in args
            assert str(SERVICE_PORT) in args
            assert "id=cam-test001" in args
            assert "paired=true" in args  # configured = paired

            svc.stop()

    def test_start_unpaired(self, unconfigured_config):
        """Unpaired camera should advertise paired=false."""
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None
            mock_popen.return_value = proc

            svc = DiscoveryService(unconfigured_config)
            svc.start()
            args = mock_popen.call_args[0][0]
            assert "paired=false" in args
            svc.stop()

    def test_start_handles_missing_avahi(self, camera_config):
        """Should handle missing avahi-publish-service gracefully."""
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            svc = DiscoveryService(camera_config)
            svc.start()
            assert svc.is_advertising is False

    def test_stop_terminates_process(self, camera_config):
        """stop() should terminate the avahi process."""
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None
            proc.wait.return_value = None
            mock_popen.return_value = proc

            svc = DiscoveryService(camera_config)
            svc.start()
            svc.stop()
            proc.terminate.assert_called_once()

    def test_stop_handles_no_process(self, camera_config):
        """stop() should not raise if no process is running."""
        svc = DiscoveryService(camera_config)
        svc.stop()  # Should not raise

    def test_service_type(self):
        """Service type should be _rtsp._tcp."""
        assert SERVICE_TYPE == "_rtsp._tcp"

    def test_service_port(self):
        """Service port should be 8554."""
        assert SERVICE_PORT == 8554
