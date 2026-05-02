# REQ: SWR-012; RISK: RISK-001; TEST: TC-005
"""Tests for camera_streamer.capture module."""

from unittest.mock import MagicMock, patch

from camera_streamer.capture import CaptureManager

# Typical ``v4l2-ctl --info`` output shapes on a Pi Zero 2W.
_CAPTURE_INFO = (
    "Driver Info:\n"
    "\tDriver name      : unicam\n"
    "\tCard type        : unicam\n"
    "Capabilities     : 0x85200001\n"
    "\t\tVideo Capture\n"
    "\t\tStreaming\n"
    "\t\tDevice Capabilities\n"
    "Device Caps      : 0x05200001\n"
    "\t\tVideo Capture\n"
    "\t\tStreaming\n"
)
# bcm2835-codec M2M node — exists on a cameraless Pi Zero 2W.
_M2M_INFO = (
    "Driver Info:\n"
    "\tDriver name      : bcm2835-codec\n"
    "Capabilities     : 0x84204000\n"
    "\t\tVideo Memory-to-Memory Multiplanar\n"
    "\t\tStreaming\n"
    "\t\tDevice Capabilities\n"
    "Device Caps      : 0x04204000\n"
    "\t\tVideo Memory-to-Memory Multiplanar\n"
    "\t\tStreaming\n"
)


def _capture_v4l2_run_mock(
    info_stdout=_CAPTURE_INFO,
    formats_stdout="H.264\n1920x1080\n",
    libcamera_list="0 : ov5647 [2592x1944 10-bit GBRG]\nAvailable cameras\n-----------------\n",
):
    """subprocess.run side_effect covering the three commands CaptureManager runs.

    - ``v4l2-ctl --info`` → info_stdout
    - ``v4l2-ctl --list-formats-ext`` → formats_stdout
    - ``libcamera-hello --list-cameras`` → libcamera_list
    """

    def _run(cmd, *args, **kwargs):
        if cmd and cmd[0] in ("libcamera-hello", "rpicam-hello"):
            return MagicMock(returncode=0, stdout=libcamera_list, stderr="")
        if "--info" in cmd:
            return MagicMock(returncode=0, stdout=info_stdout, stderr="")
        return MagicMock(returncode=0, stdout=formats_stdout, stderr="")

    return _run


class TestCaptureManager:
    """Test camera device validation."""

    def test_device_not_found(self, tmp_path):
        """Should return False when device doesn't exist."""
        mgr = CaptureManager(device=str(tmp_path / "nonexistent"))
        assert mgr.check() is False
        assert mgr.available is False
        assert "No camera module detected" in mgr.last_error

    def test_device_found_with_capture_cap(self, tmp_path):
        """Device node + V4L2 Video Capture → available."""
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch("subprocess.run", side_effect=_capture_v4l2_run_mock()):
            mgr = CaptureManager(device=str(fake_dev))
            assert mgr.check() is True
            assert mgr.available is True
            assert mgr.last_error == ""

    def test_device_exists_but_not_capture_node(self, tmp_path):
        """Device node exists but reports Video M2M, not Capture → fault.

        Regression for the "Pi Zero 2W without camera module shows
        online" bug: /dev/video10 exists as a bcm2835-codec M2M node,
        check() must reject it and raise the no-sensor banner.
        """
        fake_dev = tmp_path / "video10"
        fake_dev.write_text("")
        with patch(
            "subprocess.run", side_effect=_capture_v4l2_run_mock(info_stdout=_M2M_INFO)
        ):
            mgr = CaptureManager(device=str(fake_dev))
            assert mgr.check() is False
            assert mgr.available is False
            assert "No camera module detected" in mgr.last_error

    def test_capture_node_but_no_libcamera_sensor(self, tmp_path):
        """Video Capture node + ``No cameras available!`` → fault.

        Regression for the deeper "cameraless Pi still shows online"
        case: dtoverlay=ov5647 registers /dev/video14 as a Video
        Capture node even without the sensor physically connected.
        The V4L2 cap check passes — but libcamera-hello enumerates
        zero sensors over I2C and reports "No cameras available!".
        check() must fall back to that probe and fault out.
        """
        fake_dev = tmp_path / "video14"
        fake_dev.write_text("")
        with patch(
            "subprocess.run",
            side_effect=_capture_v4l2_run_mock(
                libcamera_list="No cameras available!\n"
            ),
        ):
            mgr = CaptureManager(device=str(fake_dev))
            assert mgr.check() is False
            assert mgr.available is False
            assert "No camera module detected" in mgr.last_error

    def test_formats_populated(self, tmp_path):
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch(
            "subprocess.run",
            side_effect=_capture_v4l2_run_mock(
                formats_stdout="[0]: 'H264' (H.264)\n  Size: 1920x1080\n  Size: 1280x720\n"
            ),
        ):
            mgr = CaptureManager(device=str(fake_dev))
            mgr.check()
            assert len(mgr.formats) > 0

    def test_supports_h264(self, tmp_path):
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch(
            "subprocess.run",
            side_effect=_capture_v4l2_run_mock(formats_stdout="H.264\n1920x1080\n"),
        ):
            mgr = CaptureManager(device=str(fake_dev))
            mgr.check()
            assert mgr.supports_h264() is True

    def test_supports_resolution(self, tmp_path):
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch(
            "subprocess.run",
            side_effect=_capture_v4l2_run_mock(formats_stdout="1920x1080\n1280x720\n"),
        ):
            mgr = CaptureManager(device=str(fake_dev))
            mgr.check()
            assert mgr.supports_resolution(1920, 1080) is True
            assert mgr.supports_resolution(3840, 2160) is False

    def test_v4l2ctl_not_found(self, tmp_path):
        """Missing v4l2-ctl → treat as present (don't regress working sensors)."""
        fake_dev = tmp_path / "video0"
        fake_dev.write_text("")
        with patch("subprocess.run", side_effect=FileNotFoundError):
            mgr = CaptureManager(device=str(fake_dev))
            assert mgr.check() is True
            assert mgr.formats == []

    def test_v4l2ctl_timeout(self, tmp_path):
        """v4l2-ctl timeout → treat as present (don't regress working sensors)."""
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


class TestExternalFaults:
    """Externally-raised fault registry (``add_fault`` / ``clear_fault``).

    Used by long-lived background tasks (the boot-time server resolver
    in #199, future thermal/storage monitors) to bubble a hardware-fault
    badge onto the heartbeat without owning their own fault registry.
    """

    def test_add_fault_appears_in_faults_list(self):
        from camera_streamer.faults import (
            FAULT_NETWORK_MDNS_RESOLUTION_FAILED,
            make_fault,
        )

        mgr = CaptureManager()
        f = make_fault(FAULT_NETWORK_MDNS_RESOLUTION_FAILED)
        mgr.add_fault(f)

        codes = [x.code for x in mgr.faults]
        assert FAULT_NETWORK_MDNS_RESOLUTION_FAILED in codes

    def test_add_fault_idempotent_on_code(self):
        """Re-adding the same code overwrites — one row per code on the wire."""
        from camera_streamer.faults import (
            FAULT_NETWORK_MDNS_RESOLUTION_FAILED,
            make_fault,
        )

        mgr = CaptureManager()
        mgr.add_fault(make_fault(FAULT_NETWORK_MDNS_RESOLUTION_FAILED))
        mgr.add_fault(
            make_fault(FAULT_NETWORK_MDNS_RESOLUTION_FAILED, context={"attempts": 5})
        )

        same_code = [
            x for x in mgr.faults if x.code == FAULT_NETWORK_MDNS_RESOLUTION_FAILED
        ]
        assert len(same_code) == 1
        # And the latest version's context wins.
        assert same_code[0].context == {"attempts": 5}

    def test_clear_fault_removes_external_only(self):
        """clear_fault must NOT touch the internal check()-managed faults."""
        from camera_streamer.faults import (
            FAULT_CAMERA_SENSOR_MISSING,
            FAULT_NETWORK_MDNS_RESOLUTION_FAILED,
            make_fault,
        )

        # Simulate a check() that ran and raised an internal fault.
        mgr = CaptureManager()
        mgr._faults = [make_fault(FAULT_CAMERA_SENSOR_MISSING)]
        mgr.add_fault(make_fault(FAULT_NETWORK_MDNS_RESOLUTION_FAILED))

        mgr.clear_fault(FAULT_NETWORK_MDNS_RESOLUTION_FAILED)

        codes = [x.code for x in mgr.faults]
        assert FAULT_CAMERA_SENSOR_MISSING in codes
        assert FAULT_NETWORK_MDNS_RESOLUTION_FAILED not in codes

    def test_clear_fault_unknown_code_is_noop(self):
        mgr = CaptureManager()
        mgr.clear_fault("does-not-exist")  # must not raise
        assert mgr.faults == []

    def test_add_fault_with_none_is_noop(self):
        """Defensive: silently ignore None rather than crashing the caller."""
        mgr = CaptureManager()
        mgr.add_fault(None)
        assert mgr.faults == []

    def test_add_fault_empty_string_code_is_noop(self):
        mgr = CaptureManager()
        mgr.clear_fault("")  # must not raise
        assert mgr.faults == []

    def test_external_faults_serialise_to_dicts(self):
        """The full faults list must round-trip through to_dict() so the
        heartbeat sender's existing serialisation path works unchanged."""
        from camera_streamer.faults import (
            FAULT_NETWORK_MDNS_RESOLUTION_FAILED,
            make_fault,
        )

        mgr = CaptureManager()
        mgr.add_fault(make_fault(FAULT_NETWORK_MDNS_RESOLUTION_FAILED))

        as_dicts = [f.to_dict() for f in mgr.faults]
        assert as_dicts[0]["code"] == FAULT_NETWORK_MDNS_RESOLUTION_FAILED
        assert as_dicts[0]["severity"] == "error"
        assert "didn't resolve" in as_dicts[0]["message"]
