# REQ: SWR-015; RISK: RISK-005; SEC: SC-004; TEST: TC-008
"""Tests for camera_streamer.discovery module."""

from unittest.mock import MagicMock, patch

from camera_streamer.discovery import SERVICE_PORT, SERVICE_TYPE, DiscoveryService


class TestDiscoveryService:
    """Test mDNS advertisement via Avahi."""

    def test_not_advertising_initially(self, camera_config):
        """Should not be advertising before start()."""
        svc = DiscoveryService(camera_config)
        assert svc.is_advertising is False

    def test_start_launches_avahi_publish(self, camera_config):
        """start() should launch avahi-publish-service, paired flag reflects PairingManager."""
        pairing = MagicMock()
        pairing.is_paired = True
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

            svc = DiscoveryService(camera_config, pairing_manager=pairing)
            svc.start()
            assert svc.is_advertising is True

            service_args = mock_popen.call_args_list[0][0][0]
            host_args = mock_popen.call_args_list[1][0][0]
            assert service_args[0] == "avahi-publish-service"
            assert SERVICE_TYPE in service_args
            assert str(SERVICE_PORT) in service_args
            assert "id=cam-test001" in service_args
            # paired flag now tracks PairingManager.is_paired, not config
            assert "paired=true" in service_args
            assert host_args[:4] == [
                "avahi-publish-address",
                "-R",
                "-f",
                "cam-test.local",
            ]
            assert host_args[4] == "192.168.1.50"

            svc.stop()

    def test_start_unpaired(self, unconfigured_config):
        """Camera without a PairingManager (or with is_paired=False) advertises paired=false."""
        pairing = MagicMock()
        pairing.is_paired = False
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

            svc = DiscoveryService(unconfigured_config, pairing_manager=pairing)
            svc.start()
            args = mock_popen.call_args_list[0][0][0]
            assert "paired=false" in args
            svc.stop()

    def test_start_no_pairing_manager_defaults_unpaired(self, camera_config):
        """Backwards-compat: no pairing_manager arg -> advertises paired=false."""
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
            service_proc.wait.return_value = None
            host_proc = MagicMock()
            host_proc.poll.return_value = None
            host_proc.wait.return_value = None
            mock_popen.side_effect = [service_proc, host_proc]

            svc = DiscoveryService(camera_config)
            svc.start()
            svc.stop()
            service_proc.terminate.assert_called_once()
            host_proc.terminate.assert_called_once()

    def test_stop_handles_no_process(self, camera_config):
        """stop() should not raise if no process is running."""
        svc = DiscoveryService(camera_config)
        svc.stop()  # Should not raise

    def test_start_skips_host_advertisement_without_network(self, camera_config):
        with (
            patch("subprocess.Popen") as mock_popen,
            patch("camera_streamer.discovery.wifi.get_hostname", return_value=""),
            patch("camera_streamer.discovery.wifi.get_ip_address", return_value=""),
        ):
            proc = MagicMock()
            proc.poll.return_value = None
            mock_popen.return_value = proc

            svc = DiscoveryService(camera_config)
            svc.start()

            assert mock_popen.call_count == 1
            svc.stop()

    def test_service_type(self):
        """Service type should be _rtsp._tcp."""
        assert SERVICE_TYPE == "_rtsp._tcp"

    def test_service_port(self):
        """Service port should be 8554."""
        assert SERVICE_PORT == 8554
