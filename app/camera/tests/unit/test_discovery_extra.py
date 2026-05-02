# REQ: SWR-015; RISK: RISK-005; SEC: SC-004; TEST: TC-008
"""Additional discovery tests for coverage."""

from unittest.mock import MagicMock, patch

from camera_streamer.discovery import VERSION, DiscoveryService


class TestDiscoveryResolution:
    """Test discovery TXT record details."""

    def test_version_string(self):
        """Version should be set."""
        assert VERSION == "1.0.0"

    def test_resolution_in_txt(self, camera_config):
        """TXT record should include resolution."""
        with (
            patch("subprocess.Popen") as mock_popen,
            patch(
                "camera_streamer.discovery.wifi.get_hostname", return_value="cam-test"
            ),
            patch(
                "camera_streamer.discovery.wifi.get_ip_address",
                return_value="192.168.1.50",
            ),
        ):
            proc = MagicMock()
            proc.poll.return_value = None
            mock_popen.side_effect = [proc, proc]

            svc = DiscoveryService(camera_config)
            svc.start()
            args = mock_popen.call_args_list[0][0][0]
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
        with (
            patch("subprocess.Popen") as mock_popen,
            patch(
                "camera_streamer.discovery.wifi.get_hostname", return_value="cam-test"
            ),
            patch(
                "camera_streamer.discovery.wifi.get_ip_address",
                return_value="192.168.1.50",
            ),
        ):
            service_proc = MagicMock()
            service_proc.poll.return_value = None
            service_proc.terminate.side_effect = OSError("already dead")
            service_proc.kill.side_effect = OSError("really dead")
            host_proc = MagicMock()
            host_proc.poll.return_value = None
            host_proc.terminate.side_effect = OSError("already dead")
            host_proc.kill.side_effect = OSError("really dead")
            mock_popen.side_effect = [service_proc, host_proc]

            svc = DiscoveryService(camera_config)
            svc.start()
            svc.stop()  # Should not raise

    def test_update_paired_status(self, camera_config):
        """update_paired_status should restart advertisement."""
        with (
            patch("subprocess.Popen") as mock_popen,
            patch(
                "camera_streamer.discovery.wifi.get_hostname", return_value="cam-test"
            ),
            patch(
                "camera_streamer.discovery.wifi.get_ip_address",
                return_value="192.168.1.50",
            ),
        ):
            service_proc_1 = MagicMock()
            service_proc_1.poll.return_value = None
            service_proc_1.wait.return_value = None
            host_proc_1 = MagicMock()
            host_proc_1.poll.return_value = None
            host_proc_1.wait.return_value = None
            service_proc_2 = MagicMock()
            service_proc_2.poll.return_value = None
            service_proc_2.wait.return_value = None
            host_proc_2 = MagicMock()
            host_proc_2.poll.return_value = None
            host_proc_2.wait.return_value = None
            mock_popen.side_effect = [
                service_proc_1,
                host_proc_1,
                service_proc_2,
                host_proc_2,
            ]

            svc = DiscoveryService(camera_config)
            svc.start()
            with patch("time.sleep"):
                svc.update_paired_status(True)
            assert mock_popen.call_count == 4
