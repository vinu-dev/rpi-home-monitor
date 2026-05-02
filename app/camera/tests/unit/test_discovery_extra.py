# REQ: SWR-015; RISK: RISK-005; SEC: SC-004; TEST: TC-008
"""Additional discovery tests for coverage."""

import io
import logging
from unittest.mock import MagicMock, patch

import pytest

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


class TestPublishReadiness:
    """Verify the avahi-publish readiness check (issue #198).

    Without these guards, ``Popen`` returning a process handle was
    treated as success even when avahi-daemon refused the publication
    or the helper crashed before reaching the wire — operators saw
    "advertisement started" in the log while the camera was invisible
    on the network at boot.
    """

    def _proc(self, *, exit_rc=None, stderr_bytes=b""):
        """Build a fake avahi-publish process.

        ``exit_rc=None`` means the process is alive across all polls.
        ``exit_rc=<int>`` makes it appear already-exited on the first
        poll, with the supplied stderr bytes available for capture.
        """
        proc = MagicMock()
        proc.poll.return_value = exit_rc
        proc.returncode = exit_rc if exit_rc is not None else 0
        proc.stderr = io.BytesIO(stderr_bytes) if stderr_bytes else io.BytesIO(b"")
        proc.wait.return_value = None
        return proc

    def test_service_exits_immediately_marks_not_advertising(
        self, camera_config, monkeypatch, caplog
    ):
        """A dead service-publish process must not look like a healthy advertiser."""
        # The conftest autouse fixture zeroes the readiness timeout for
        # speed; this test deliberately bumps it back up to a small but
        # non-zero window so the loop body actually runs.
        monkeypatch.setattr(DiscoveryService, "PUBLISH_READINESS_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(DiscoveryService, "PUBLISH_READINESS_POLL_INTERVAL", 0.01)

        dead = self._proc(
            exit_rc=2, stderr_bytes=b"Failed to register service: bus not ready\n"
        )
        with (
            patch("subprocess.Popen", return_value=dead),
            patch(
                "camera_streamer.discovery.wifi.get_hostname", return_value="cam-test"
            ),
            patch(
                "camera_streamer.discovery.wifi.get_ip_address",
                return_value="192.168.1.50",
            ),
            caplog.at_level(logging.ERROR, logger="camera-streamer.discovery"),
        ):
            svc = DiscoveryService(camera_config)
            svc.start()

            # The service handle is dropped so is_advertising reports the
            # truth and the outer watchdog can retry.
            assert svc.is_advertising is False
            assert svc._process is None
            assert svc._running is False

            # And the actual error reached the log so operators have a
            # diagnostic instead of a silent failure.
            assert any(
                "avahi-publish-service exited immediately" in r.message
                and "bus not ready" in r.message
                for r in caplog.records
            ), [r.message for r in caplog.records]

    def test_host_exits_immediately_keeps_service_advertisement(
        self, camera_config, monkeypatch, caplog
    ):
        """A host-record failure must not collapse the whole service-browse advertisement."""
        monkeypatch.setattr(DiscoveryService, "PUBLISH_READINESS_TIMEOUT_SECONDS", 0.05)
        monkeypatch.setattr(DiscoveryService, "PUBLISH_READINESS_POLL_INTERVAL", 0.01)

        live_service = self._proc(exit_rc=None)
        dead_host = self._proc(
            exit_rc=1, stderr_bytes=b"Failed to add address: name collision\n"
        )

        with (
            patch("subprocess.Popen", side_effect=[live_service, dead_host]),
            patch(
                "camera_streamer.discovery.wifi.get_hostname", return_value="cam-test"
            ),
            patch(
                "camera_streamer.discovery.wifi.get_ip_address",
                return_value="192.168.1.50",
            ),
            caplog.at_level(logging.ERROR, logger="camera-streamer.discovery"),
        ):
            svc = DiscoveryService(camera_config)
            svc.start()

            # Service browse is still alive — a name-collision on the A
            # record is recoverable, the camera is still findable.
            assert svc.is_advertising is True
            # ...but the dead host handle is dropped so we don't try to
            # terminate a phantom process at shutdown.
            assert svc._host_process is None

            assert any(
                "avahi-publish-host exited immediately" in r.message
                and "name collision" in r.message
                for r in caplog.records
            ), [r.message for r in caplog.records]

            svc.stop()

    def test_alive_process_passes_readiness_check(self, camera_config):
        """The happy path: both publishers stay alive across the readiness window."""
        # Default conftest fast-paths the timeout to 0.0 already; this
        # test verifies the contract for the fast path.
        live_service = self._proc(exit_rc=None)
        live_host = self._proc(exit_rc=None)
        with (
            patch("subprocess.Popen", side_effect=[live_service, live_host]),
            patch(
                "camera_streamer.discovery.wifi.get_hostname", return_value="cam-test"
            ),
            patch(
                "camera_streamer.discovery.wifi.get_ip_address",
                return_value="192.168.1.50",
            ),
        ):
            svc = DiscoveryService(camera_config)
            svc.start()
            assert svc.is_advertising is True
            assert svc._host_process is live_host
            svc.stop()

    def test_readiness_check_handles_missing_stderr(
        self, camera_config, monkeypatch, caplog
    ):
        """A process exited but with stderr=None must not raise — log says <no stderr>."""
        monkeypatch.setattr(DiscoveryService, "PUBLISH_READINESS_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(DiscoveryService, "PUBLISH_READINESS_POLL_INTERVAL", 0.01)

        dead = MagicMock()
        dead.poll.return_value = 1
        dead.returncode = 1
        dead.stderr = None  # explicit "no pipe" — must not crash

        with (
            patch("subprocess.Popen", return_value=dead),
            caplog.at_level(logging.ERROR, logger="camera-streamer.discovery"),
        ):
            svc = DiscoveryService(camera_config)
            svc.start()

            assert svc.is_advertising is False
            assert any("<no stderr>" in r.message for r in caplog.records), [
                r.message for r in caplog.records
            ]

    def test_readiness_check_returns_true_for_alive_process(self, camera_config):
        """Direct unit test of _verify_publish_alive happy path."""
        live = self._proc(exit_rc=None)
        svc = DiscoveryService(camera_config)
        # Force a non-zero window so the loop runs at least one iteration.
        svc.PUBLISH_READINESS_TIMEOUT_SECONDS = 0.02
        svc.PUBLISH_READINESS_POLL_INTERVAL = 0.005
        assert svc._verify_publish_alive(live, "service") is True

    @pytest.mark.parametrize(
        "stderr,expected_fragment",
        [
            (b"Failed to register service: bus not ready\n", "bus not ready"),
            (b"D-Bus error: AccessDenied\n", "D-Bus error"),
            (b"", "<no stderr>"),
        ],
    )
    def test_failure_log_quotes_stderr(
        self, camera_config, monkeypatch, caplog, stderr, expected_fragment
    ):
        """Operators need the actual avahi error in the log to debug boot races."""
        monkeypatch.setattr(DiscoveryService, "PUBLISH_READINESS_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(DiscoveryService, "PUBLISH_READINESS_POLL_INTERVAL", 0.01)
        dead = self._proc(exit_rc=1, stderr_bytes=stderr)
        with (
            patch("subprocess.Popen", return_value=dead),
            caplog.at_level(logging.ERROR, logger="camera-streamer.discovery"),
        ):
            svc = DiscoveryService(camera_config)
            svc.start()
            assert any(expected_fragment in r.message for r in caplog.records), [
                r.message for r in caplog.records
            ]
