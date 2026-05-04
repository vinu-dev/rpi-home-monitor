# REQ: SWR-062; RISK: RISK-001, RISK-008; TEST: TC-005, TC-047
"""Integration coverage for camera lifecycle watchdog wiring."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from camera_streamer.lifecycle import CameraLifecycle


def _make_config(**overrides):
    defaults = dict(
        server_ip="192.168.1.100",
        server_https_url="https://192.168.1.100",
        camera_id="cam-test",
        data_dir="/tmp/test",
        is_configured=True,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _make_platform(**overrides):
    defaults = dict(
        camera_device="/dev/video0",
        wifi_interface="wlan0",
        led_path="/sys/class/leds/ACT",
        thermal_path="/sys/class/thermal/thermal_zone0",
        hostname_prefix="homecam",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_setup_marks_ready_and_beats():
    config = _make_config()
    platform = _make_platform()
    notifier = MagicMock()
    lifecycle = CameraLifecycle(config, platform, lambda: False, notifier=notifier)

    call_count = [0]

    def needs_setup():
        call_count[0] += 1
        return call_count[0] < 3

    setup = MagicMock()
    setup.needs_setup.side_effect = needs_setup

    with (
        patch("camera_streamer.lifecycle.WifiSetupServer", return_value=setup),
        patch.object(lifecycle, "_start_hotspot"),
        patch("camera_streamer.lifecycle.time.sleep"),
    ):
        assert lifecycle._do_setup() is True

    notifier.mark_ready.assert_called_once()
    notifier.beat.assert_any_call("setup")


def test_pairing_marks_ready_and_beats():
    config = _make_config()
    platform = _make_platform()
    notifier = MagicMock()
    lifecycle = CameraLifecycle(config, platform, lambda: False, notifier=notifier)

    class PairingState:
        def __init__(self):
            self._checks = 0

        @property
        def is_paired(self):
            self._checks += 1
            return self._checks > 1

    lifecycle._pairing = PairingState()

    with (
        patch("camera_streamer.lifecycle.CameraStatusServer"),
        patch.object(lifecycle, "_register_with_server"),
        patch("camera_streamer.lifecycle.time.sleep"),
    ):
        assert lifecycle._do_pairing() is True

    notifier.mark_ready.assert_called_once()
    notifier.beat.assert_any_call("pairing")


def test_wait_for_wifi_records_connecting_beats():
    config = _make_config()
    platform = _make_platform()
    notifier = MagicMock()
    lifecycle = CameraLifecycle(config, platform, lambda: False, notifier=notifier)

    result = MagicMock(stdout="IP4.ADDRESS[1]:192.168.1.50/24\n")
    with patch("camera_streamer.lifecycle.subprocess.run", return_value=result):
        assert lifecycle._wait_for_wifi() is True

    notifier.beat.assert_called_with("connecting")


@patch("camera_streamer.lifecycle.led")
@patch("camera_streamer.lifecycle.HealthMonitor")
@patch("camera_streamer.lifecycle.CameraStatusServer")
@patch("camera_streamer.lifecycle.StreamManager")
@patch("camera_streamer.lifecycle.DiscoveryService")
def test_running_marks_ready_and_passes_notifier_to_health(
    MockDiscovery,
    MockStream,
    MockStatus,
    MockHealth,
    mock_led,
    tmp_path,
):
    config = _make_config()
    platform = _make_platform()
    notifier = MagicMock()

    calls = [0]

    def shutdown():
        calls[0] += 1
        return calls[0] > 1

    lifecycle = CameraLifecycle(config, platform, shutdown, notifier=notifier)
    lifecycle._capture = MagicMock()
    state_file = tmp_path / "stream_state"
    state_file.write_text("running")
    lifecycle._stream_state_path = str(state_file)

    assert lifecycle._do_running() is True

    notifier.mark_ready.assert_called_once()
    notifier.beat.assert_any_call("lifecycle")
    assert MockHealth.call_args.kwargs["notifier"] is notifier
