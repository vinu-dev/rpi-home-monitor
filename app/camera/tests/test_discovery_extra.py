"""Additional discovery tests for coverage."""
from unittest.mock import patch, MagicMock

from camera_streamer.discovery import DiscoveryService, VERSION


class TestDiscoveryResolution:
    """Test discovery TXT record details."""

    def test_version_string(self):
        """Version should be set."""
        assert VERSION == "1.0.0"

    def test_resolution_in_txt(self, camera_config):
        """TXT record should include resolution."""
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None
            mock_popen.return_value = proc

            svc = DiscoveryService(camera_config)
            svc.start()
            args = mock_popen.call_args[0][0]
            assert "resolution=1920x1080" in args
            svc.stop()

    def test_start_handles_oserror(self, camera_config):
        """Should handle OSError during Popen."""
        with patch("subprocess.Popen", side_effect=OSError("fail")):
            svc = DiscoveryService(camera_config)
            svc.start()
            assert svc.is_advertising is False

    def test_stop_handles_kill_failure(self, camera_config):
        """stop() should handle kill failure."""
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None
            import subprocess
            proc.terminate.side_effect = OSError("already dead")
            proc.kill.side_effect = OSError("really dead")
            mock_popen.return_value = proc

            svc = DiscoveryService(camera_config)
            svc.start()
            svc.stop()  # Should not raise

    def test_update_paired_status(self, camera_config):
        """update_paired_status should restart advertisement."""
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None
            proc.wait.return_value = None
            mock_popen.return_value = proc

            svc = DiscoveryService(camera_config)
            svc.start()
            with patch("time.sleep"):
                svc.update_paired_status(True)
            # Should have been called twice (start + restart)
            assert mock_popen.call_count >= 2
