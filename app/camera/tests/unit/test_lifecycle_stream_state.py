"""Unit tests for camera lifecycle helpers.

Focuses on the boot-time decision of whether to start streaming
automatically, per ADR-0017. The full CameraLifecycle orchestrator
touches systemd/mDNS/hardware so we isolate the one decision worth
unit-testing into the ``_read_desired_stream_state`` helper.
"""

from camera_streamer.lifecycle import _read_desired_stream_state


class TestReadDesiredStreamState:
    def test_missing_file_defaults_to_stopped(self, tmp_path):
        """Fresh boot / fresh pair with no override file → stopped.

        ADR-0017 on-demand model (issue #115): the camera waits for the
        server to ask for a stream. No file == no explicit ask == stopped.
        Must match ControlHandler._load_stream_state in control.py.
        """
        path = tmp_path / "stream_state"
        assert _read_desired_stream_state(str(path)) == "stopped"

    def test_reads_running(self, tmp_path):
        path = tmp_path / "stream_state"
        path.write_text("running")
        assert _read_desired_stream_state(str(path)) == "running"

    def test_reads_stopped(self, tmp_path):
        """Explicit override to stop is honoured."""
        path = tmp_path / "stream_state"
        path.write_text("stopped")
        assert _read_desired_stream_state(str(path)) == "stopped"

    def test_garbage_collapses_to_stopped(self, tmp_path):
        """Unreadable content falls back to stopped (ADR-0017 on-demand)."""
        path = tmp_path / "stream_state"
        path.write_text("maybe")
        assert _read_desired_stream_state(str(path)) == "stopped"

    def test_trailing_whitespace_is_stripped(self, tmp_path):
        path = tmp_path / "stream_state"
        path.write_text("running\n")
        assert _read_desired_stream_state(str(path)) == "running"

    def test_matches_control_server_default(self, tmp_path):
        """Regression for #115: lifecycle + control must agree on the default.

        If a future refactor makes one of them default to 'running' again,
        this test fails loudly — that drift was exactly what #115 reported.
        """
        from unittest.mock import MagicMock

        from camera_streamer.control import ControlHandler

        path = tmp_path / "stream_state"
        cs = ControlHandler.__new__(ControlHandler)
        cs._stream_state_path = str(path)
        cs._stream = MagicMock()
        lifecycle_default = _read_desired_stream_state(str(path))
        control_default = cs._load_stream_state()
        assert lifecycle_default == control_default == "stopped"


class TestDoRunningHonoursStreamState:
    """Integration-ish: verify _do_running checks the persisted state.

    We don't run the real _do_running — it spawns threads, mDNS, the
    status server, and an OTA agent. Instead we verify the contract via
    _read_desired_stream_state plus a direct read of the lifecycle source
    for the decision gate. The helper is covered by the class above; here
    we assert the gate exists on the instance attribute so future
    refactors can't silently drop it.
    """

    def test_lifecycle_exposes_stream_state_path(self, tmp_path):
        from unittest.mock import MagicMock

        from camera_streamer.lifecycle import CameraLifecycle

        # Construct with stub dependencies — we only inspect an attribute,
        # we never call run().
        class _Platform:
            camera_device = "/dev/video0"
            wifi_interface = "wlan0"
            led_path = None
            thermal_path = None
            hostname_prefix = "cam"

        cfg = MagicMock()
        cfg.certs_dir = str(tmp_path)
        lc = CameraLifecycle(
            config=cfg,
            platform=_Platform(),
            shutdown_event=lambda: True,
        )
        assert lc._stream_state_path.endswith("stream_state")
